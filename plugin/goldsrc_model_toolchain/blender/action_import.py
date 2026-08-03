"""Deterministic GoldSrc local-channel to Blender Action conversion."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import bpy
from mathutils import Euler, Matrix, Vector

from ..core.action_curves import representative_frame_samples
from ..core.errors import ToolchainError


def local_pose_globals(bones, poses, scale: float) -> list[Matrix]:
    """Rebuild armature-space pose matrices from GoldSrc parent-local channels."""

    globals_by_index: list[Matrix] = []
    for bone, pose in zip(bones, poses):
        position, rotation = pose
        local = (
            Matrix.Translation(Vector(position) * scale)
            @ Euler(rotation, "XYZ").to_matrix().to_4x4()
        )
        target = globals_by_index[bone.parent] @ local if bone.parent >= 0 else local
        globals_by_index.append(target)
    return globals_by_index


def _basis_from_pose(pose_bone, target: Matrix, parent_target: Matrix | None) -> Matrix:
    kwargs = {}
    if pose_bone.parent is not None:
        if parent_target is None:
            raise ValueError(f"parent pose is missing for {pose_bone.name}")
        kwargs = {
            "parent_matrix": parent_target,
            "parent_matrix_local": pose_bone.parent.bone.matrix_local,
        }
    return pose_bone.bone.convert_local_to_pose(
        target,
        pose_bone.bone.matrix_local,
        invert=True,
        **kwargs,
    )


def _channelbag(action, armature):
    layer = action.layers.new("Layer")
    strip = layer.strips.new()
    slot = action.slots.new("OBJECT", armature.name)
    return slot, strip.channelbags.new(slot)


def create_action_from_local_frames(
    armature,
    bones,
    frames: Mapping[int, Sequence[tuple[Sequence[float], Sequence[float]]]],
    *,
    name: str,
    scale: float,
):
    """Create an Action against the target armature's real rest/parent space."""

    if not frames:
        return None
    ordered_frames = sorted(frames)
    action = bpy.data.actions.new(name)
    action.use_fake_user = True
    slot, bag = _channelbag(action, armature)
    armature.animation_data_create()
    armature.animation_data.action = action
    armature.animation_data.action_slot = slot
    curves = {}
    previous_quaternions = {}
    try:
        for bone in bones:
            pose_bone = armature.pose.bones.get(bone.name)
            if pose_bone is None:
                raise ToolchainError(
                    "IMPORT", "animation.target_bone", "Target armature is missing a required bone",
                    {"bone": bone.name, "armature": armature.name},
                )
            pose_bone.rotation_mode = "QUATERNION"
            escaped = bone.name.replace("\\", "\\\\").replace('"', '\\"')
            path = f'pose.bones["{escaped}"].'
            location = [bag.fcurves.new(path + "location", index=index) for index in range(3)]
            rotation = [bag.fcurves.new(path + "rotation_quaternion", index=index) for index in range(4)]
            for curve in location + rotation:
                curve.keyframe_points.add(len(ordered_frames))
            curves[bone.index] = location, rotation

        for point_index, frame in enumerate(ordered_frames):
            targets = local_pose_globals(bones, frames[frame], scale)
            for bone, target in zip(bones, targets):
                pose_bone = armature.pose.bones[bone.name]
                parent_target = targets[bone.parent] if bone.parent >= 0 else None
                basis = _basis_from_pose(pose_bone, target, parent_target)
                location, quaternion, _basis_scale = basis.decompose()
                previous = previous_quaternions.get(bone.index)
                if previous is not None and previous.dot(quaternion) < 0.0:
                    quaternion.negate()
                previous_quaternions[bone.index] = quaternion.copy()
                location_curves, rotation_curves = curves[bone.index]
                for index in range(3):
                    location_curves[index].keyframe_points[point_index].co = (frame, location[index])
                for index in range(4):
                    rotation_curves[index].keyframe_points[point_index].co = (frame, quaternion[index])
        for location, rotation in curves.values():
            for curve in location + rotation:
                curve.update()
        return action
    except Exception:
        bpy.data.actions.remove(action)
        raise


def audit_action_pose_matrices(
    armature,
    action,
    bones,
    frames: Mapping[int, Sequence[tuple[Sequence[float], Sequence[float]]]],
    *,
    scale: float,
    position_tolerance: float = 0.0005,
    rotation_tolerance: float = 0.0005,
) -> dict:
    """Compare evaluated Action bones with source global matrices at five samples."""

    armature.animation_data_create()
    armature.animation_data.action = action
    if action.slots:
        armature.animation_data.action_slot = action.slots[0]
    source_frames = sorted(frames)
    requested = representative_frame_samples((source_frames[0], source_frames[-1]), maximum=5)
    samples = sorted({min(source_frames, key=lambda value: abs(value - requested_frame)) for requested_frame in requested})
    maximum_position = 0.0
    maximum_rotation = 0.0
    worst = None
    for frame in samples:
        bpy.context.scene.frame_set(int(frame))
        expected = local_pose_globals(bones, frames[frame], scale)
        for bone, target in zip(bones, expected):
            actual = armature.pose.bones[bone.name].matrix.copy()
            position_error = (actual.translation - target.translation).length
            rotation_error = actual.to_quaternion().rotation_difference(target.to_quaternion()).angle
            if position_error > maximum_position or rotation_error > maximum_rotation:
                worst = {"frame": frame, "bone": bone.name}
            maximum_position = max(maximum_position, position_error)
            maximum_rotation = max(maximum_rotation, rotation_error)
    report = {
        "status": "pass" if maximum_position <= position_tolerance and maximum_rotation <= rotation_tolerance else "fail",
        "samples": samples,
        "max_position_error": maximum_position,
        "max_rotation_error_radians": maximum_rotation,
        "position_tolerance": position_tolerance,
        "rotation_tolerance_radians": rotation_tolerance,
        "worst": worst,
    }
    if report["status"] != "pass":
        raise ToolchainError(
            "ROUNDTRIP", "roundtrip.bone_matrix_error",
            "Readback Action diverges from decoded MDL bone matrices", report,
        )
    return report
