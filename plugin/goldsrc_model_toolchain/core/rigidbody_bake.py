"""Adaptive rigid-body capture and diagnostics for Blender-authored GoldSrc animation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from math import tau
from typing import Any, Mapping, Sequence

from mathutils import Euler, Matrix, Vector

from .action_curves import iter_action_fcurves
from .physics_config import audit_constraint_topology, configure_rigidbody_world
from .physics_events import evaluate_event_chain


@dataclass(frozen=True)
class SettlementConfig:
    frame_start: int
    max_frame: int
    activity_after_frame: int
    consecutive_frames: int = 12
    hold_frames: int = 5
    translation_epsilon: float = 0.002
    rotation_epsilon: float = 0.002
    receiver_bounds: tuple[float, float, float, float, float, float] | None = None
    receiver_margin: float = 0.05
    diagnostics_limit: int = 5

    def validate(self) -> None:
        if self.frame_start < 0 or self.max_frame <= self.frame_start:
            raise ValueError("max_frame must be greater than frame_start")
        if not self.frame_start <= self.activity_after_frame < self.max_frame:
            raise ValueError("activity_after_frame must be inside the simulation range")
        if self.consecutive_frames < 2 or self.hold_frames < 0:
            raise ValueError("consecutive_frames must be >= 2 and hold_frames must be >= 0")
        if self.translation_epsilon < 0.0 or self.rotation_epsilon < 0.0:
            raise ValueError("settlement thresholds must be non-negative")
        if self.receiver_bounds is not None and len(self.receiver_bounds) != 6:
            raise ValueError("receiver_bounds must contain min xyz followed by max xyz")


@dataclass
class SimulationCapture:
    matrices: dict[int, dict[str, Matrix]]
    animation_bounds: list[float]
    report: dict[str, Any]
    samples: dict[int, dict[str, dict[str, Any]]] = field(default_factory=dict)

    @property
    def frame_start(self) -> int:
        return int(self.report["frame_range"][0])

    @property
    def frame_end(self) -> int:
        return int(self.report["frame_range"][1])


def rigidbody_world_delta(initial_world: Matrix, current_world: Matrix) -> Matrix:
    """Return the solver-owned world-space deformation from the initial pose."""

    return current_world @ initial_world.inverted_safe()


def pose_basis_from_armature_matrix(
    pose_bone: Any,
    pose_matrix: Matrix,
    *,
    parent_pose_matrix: Matrix | None = None,
) -> Matrix:
    """Convert an armature-space pose target to keyframeable local channels."""

    bone = pose_bone.bone
    if pose_bone.parent is None:
        return bone.convert_local_to_pose(
            pose_matrix,
            bone.matrix_local,
            invert=True,
        )
    parent_matrix = parent_pose_matrix or pose_bone.parent.matrix
    return bone.convert_local_to_pose(
        pose_matrix,
        bone.matrix_local,
        parent_matrix=parent_matrix,
        parent_matrix_local=pose_bone.parent.bone.matrix_local,
        invert=True,
    )


def apply_rigidbody_world_transform(
    pose_bone: Any,
    initial_world: Matrix,
    current_world: Matrix,
    *,
    armature_world: Matrix | None = None,
    parent_pose_matrix: Matrix | None = None,
) -> Matrix:
    """Apply a rigid-body transform without confusing world and bone-local space.

    The target is chosen so that the armature deformation matrix reproduces the
    rigid-body world delta exactly: pose_matrix @ rest_matrix^-1 == delta.
    """

    armature_world = armature_world or Matrix.Identity(4)
    world_delta = rigidbody_world_delta(initial_world, current_world)
    armature_delta = armature_world.inverted_safe() @ world_delta @ armature_world
    target_pose_matrix = armature_delta @ pose_bone.bone.matrix_local
    pose_bone.rotation_mode = "QUATERNION"
    pose_bone.matrix_basis = pose_basis_from_armature_matrix(
        pose_bone,
        target_pose_matrix,
        parent_pose_matrix=parent_pose_matrix,
    )
    return target_pose_matrix


def write_capture_matrices(capture: SimulationCapture, output_path: Path | str) -> Path:
    """Persist the authoritative per-frame rigid-body matrices for export audits."""

    path = Path(output_path).expanduser().resolve()
    payload = {
        "version": 1,
        "frame_range": [capture.frame_start, capture.frame_end],
        "matrices": {
            str(frame): {
                name: [list(row) for row in matrix]
                for name, matrix in objects.items()
            }
            for frame, objects in sorted(capture.matrices.items())
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def read_capture_matrices(input_path: Path | str) -> dict[int, dict[str, Matrix]]:
    """Read matrices written by :func:`write_capture_matrices`."""

    path = Path(input_path).expanduser().resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1 or not isinstance(payload.get("matrices"), Mapping):
        raise ValueError(f"invalid rigid-body matrix capture: {path}")
    return {
        int(frame): {
            str(name): Matrix(rows)
            for name, rows in objects.items()
        }
        for frame, objects in payload["matrices"].items()
    }


def audit_armature_rigidbody_transfer(
    scene: Any,
    mesh_object: Any,
    armature: Any,
    action: Any,
    matrices: Mapping[int, Mapping[str, Matrix]],
    frame_map: Mapping[int, int],
    object_names: Sequence[str],
    *,
    position_tolerance: float = 0.0005,
) -> dict[str, Any]:
    """Compare evaluated, armature-deformed vertices with rigid-body capture."""

    names = [str(name) for name in object_names]
    group_names = {group.index: group.name for group in mesh_object.vertex_groups}
    indices: dict[str, list[int]] = {name: [] for name in names}
    for vertex in mesh_object.data.vertices:
        for membership in vertex.groups:
            name = group_names.get(membership.group)
            if name in indices and membership.weight > 0.999:
                indices[name].append(vertex.index)
    missing = [name for name, values in indices.items() if not values]
    if missing:
        return {
            "status": "fail",
            "issues": [f"missing single-weight vertices for {name}" for name in missing],
            "max_position_error": None,
        }

    original_action = armature.animation_data.action if armature.animation_data else None
    original_frame = int(scene.frame_current)
    source_world = mesh_object.matrix_world.copy()
    source_vertices = [source_world @ vertex.co for vertex in mesh_object.data.vertices]
    initial_frame = min(matrices)
    object_reports = {
        name: {"max_position_error": 0.0, "frame": None, "vertex": None}
        for name in names
    }
    worst = {"error": 0.0, "frame": None, "source_frame": None, "object": None, "vertex": None}
    issues: list[str] = []
    try:
        armature.animation_data.action = action
        for output_frame, source_frame in sorted(frame_map.items()):
            if source_frame not in matrices:
                issues.append(f"capture frame {source_frame} is missing")
                continue
            scene.frame_set(int(output_frame))
            scene.view_layers[0].update()
            depsgraph = __import__("bpy").context.evaluated_depsgraph_get()
            evaluated = mesh_object.evaluated_get(depsgraph)
            evaluated_mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
            try:
                for name in names:
                    if name not in matrices[initial_frame] or name not in matrices[source_frame]:
                        issues.append(f"capture object {name} is missing at frame {source_frame}")
                        continue
                    delta = rigidbody_world_delta(
                        matrices[initial_frame][name],
                        matrices[source_frame][name],
                    )
                    for vertex_index in indices[name]:
                        expected = delta @ source_vertices[vertex_index]
                        actual = evaluated.matrix_world @ evaluated_mesh.vertices[vertex_index].co
                        error = float((actual - expected).length)
                        if error > object_reports[name]["max_position_error"]:
                            object_reports[name] = {
                                "max_position_error": error,
                                "frame": int(output_frame),
                                "source_frame": int(source_frame),
                                "vertex": int(vertex_index),
                            }
                        if error > worst["error"]:
                            worst = {
                                "error": error,
                                "frame": int(output_frame),
                                "source_frame": int(source_frame),
                                "object": name,
                                "vertex": int(vertex_index),
                            }
            finally:
                evaluated.to_mesh_clear()
    finally:
        armature.animation_data.action = original_action
        scene.frame_set(original_frame)
        scene.view_layers[0].update()

    if worst["error"] > position_tolerance:
        issues.append(
            "armature-deformed geometry diverges from rigid-body capture: "
            f"{worst['error']:.6f} > {position_tolerance:.6f}"
        )
    return {
        "status": "pass" if not issues else "fail",
        "issues": list(dict.fromkeys(issues)),
        "method": "all single-weight vertices after evaluated Armature modifier",
        "frames_checked": len(frame_map),
        "objects_checked": len(names),
        "position_tolerance": float(position_tolerance),
        "max_position_error": float(worst["error"]),
        "worst": worst,
        "objects": object_reports,
    }


def _smd_global_matrices(document: Any, frame: int) -> dict[int, Matrix]:
    poses = {pose.bone: pose for pose in document.frames[frame]}
    bones = {bone.index: bone for bone in document.bones}
    result: dict[int, Matrix] = {}

    def resolve(bone_id: int) -> Matrix:
        if bone_id in result:
            return result[bone_id]
        bone = bones[bone_id]
        pose = poses[bone_id]
        local = Matrix.LocRotScale(
            Vector(pose.position),
            Euler(pose.rotation, "XYZ").to_quaternion(),
            Vector((1.0, 1.0, 1.0)),
        )
        result[bone_id] = local if bone.parent == -1 else resolve(bone.parent) @ local
        return result[bone_id]

    for bone_id in bones:
        resolve(bone_id)
    return result


def audit_smd_rigidbody_transfer(
    reference_path: Path | str,
    animation_path: Path | str,
    matrices: Mapping[int, Mapping[str, Matrix]],
    frame_map: Mapping[int, int],
    object_names: Sequence[str],
    *,
    world_to_smd: Matrix | None = None,
    position_tolerance: float = 0.002,
) -> dict[str, Any]:
    """Rebuild SMD hierarchy transforms and compare them with the solver capture."""

    from .smd import read_smd, validate_smd

    reference = read_smd(reference_path)
    animation = read_smd(animation_path)
    issues = validate_smd(reference, require_triangles=True)
    issues.extend(validate_smd(animation, require_triangles=False))
    reference_names = {bone.name: bone.index for bone in reference.bones}
    animation_names = {bone.name: bone.index for bone in animation.bones}
    if reference_names != animation_names:
        issues.append("reference and animation SMD bone maps differ")
    names = [str(name) for name in object_names]
    missing = [name for name in names if name not in reference_names]
    issues.extend(f"SMD bone is missing for {name}" for name in missing)
    if issues:
        return {"status": "fail", "issues": list(dict.fromkeys(issues))}

    reference_frame = min(reference.frames)
    reference_global = _smd_global_matrices(reference, reference_frame)
    vertices: dict[int, list[Vector]] = {bone_id: [] for bone_id in reference_names.values()}
    for triangle in reference.triangles:
        for vertex in triangle.vertices:
            vertices.setdefault(vertex.bone, []).append(Vector(vertex.position))
    space = world_to_smd or Matrix.Identity(4)
    space_inverse = space.inverted_safe()
    initial_frame = min(matrices)
    worst = {"error": 0.0, "frame": None, "source_frame": None, "object": None}
    object_reports = {name: {"max_position_error": 0.0, "frame": None} for name in names}
    for output_frame, source_frame in sorted(frame_map.items()):
        if output_frame not in animation.frames:
            issues.append(f"animation SMD frame {output_frame} is missing")
            continue
        if source_frame not in matrices:
            issues.append(f"capture frame {source_frame} is missing")
            continue
        animation_global = _smd_global_matrices(animation, output_frame)
        for name in names:
            bone_id = reference_names[name]
            if not vertices.get(bone_id):
                issues.append(f"reference SMD has no vertices weighted to {name}")
                continue
            expected_delta = (
                space
                @ rigidbody_world_delta(matrices[initial_frame][name], matrices[source_frame][name])
                @ space_inverse
            )
            actual_delta = animation_global[bone_id] @ reference_global[bone_id].inverted_safe()
            for vertex in vertices[bone_id]:
                error = float(((actual_delta @ vertex) - (expected_delta @ vertex)).length)
                if error > object_reports[name]["max_position_error"]:
                    object_reports[name] = {
                        "max_position_error": error,
                        "frame": int(output_frame),
                        "source_frame": int(source_frame),
                    }
                if error > worst["error"]:
                    worst = {
                        "error": error,
                        "frame": int(output_frame),
                        "source_frame": int(source_frame),
                        "object": name,
                    }
    if worst["error"] > position_tolerance:
        issues.append(
            "SMD hierarchy reconstruction diverges from rigid-body capture: "
            f"{worst['error']:.6f} > {position_tolerance:.6f}"
        )
    return {
        "status": "pass" if not issues else "fail",
        "issues": list(dict.fromkeys(issues)),
        "method": "reference/animation SMD hierarchy reconstruction over all weighted vertices",
        "frames_checked": len(frame_map),
        "objects_checked": len(names),
        "position_tolerance": float(position_tolerance),
        "max_position_error": float(worst["error"]),
        "worst": worst,
        "objects": object_reports,
    }


def _rotation_delta(previous: Matrix, current: Matrix) -> float:
    angle = float(previous.to_quaternion().rotation_difference(current.to_quaternion()).angle)
    return min(angle, abs(tau - angle))


def _world_bounds(obj: Any, matrix: Matrix) -> tuple[Vector, Vector]:
    points = [matrix @ Vector(corner) for corner in obj.bound_box]
    minimum = Vector(tuple(min(point[axis] for point in points) for axis in range(3)))
    maximum = Vector(tuple(max(point[axis] for point in points) for axis in range(3)))
    return minimum, maximum


def _obb_sample(obj: Any, matrix: Matrix, rotation_delta: float = 0.0) -> dict[str, Any]:
    """Convert a Blender object's transformed local bounds to serializable OBB facts."""

    local = [Vector(corner) for corner in obj.bound_box]
    local_min = Vector(tuple(min(point[axis] for point in local) for axis in range(3)))
    local_max = Vector(tuple(max(point[axis] for point in local) for axis in range(3)))
    local_center = (local_min + local_max) * 0.5
    local_half = (local_max - local_min) * 0.5
    basis = matrix.to_3x3()
    axes: list[list[float]] = []
    half_sizes: list[float] = []
    for axis in range(3):
        column = Vector(basis.col[axis])
        scale = column.length
        axes.append(list(column.normalized() if scale > 1e-9 else Vector((0.0, 0.0, 0.0))))
        half_sizes.append(float(local_half[axis] * scale))
    center = matrix @ local_center
    sample = {
        "center": list(center),
        "location": list(center),
        "rotation_delta": float(rotation_delta),
        "rotation_quaternion": list(matrix.to_quaternion()),
        "obb": {"center": list(center), "axes": axes, "half_sizes": half_sizes},
    }
    data = getattr(obj, "data", None)
    if data is not None and hasattr(data, "vertices") and hasattr(data, "polygons"):
        sample["convex"] = {
            "vertices": [list(matrix @ vertex.co) for vertex in data.vertices],
            "faces": [list(polygon.vertices) for polygon in data.polygons],
        }
    return sample


def _convex_axes(convex: Mapping[str, Any]) -> tuple[list[Vector], list[Vector]]:
    vertices = [Vector(tuple(float(component) for component in point)) for point in convex.get("vertices", [])]
    face_axes: list[Vector] = []
    edge_axes: list[Vector] = []
    seen_faces: set[tuple[float, float, float]] = set()
    seen_edges: set[tuple[float, float, float]] = set()

    def append_unique(target, seen, vector):
        if vector.length <= 1e-9:
            return
        axis = vector.normalized()
        if next((component for component in axis if abs(component) > 1e-8), 1.0) < 0.0:
            axis.negate()
        key = tuple(round(float(component), 5) for component in axis)
        if key not in seen:
            seen.add(key)
            target.append(axis)

    for face in convex.get("faces", []):
        if len(face) < 3:
            continue
        points = [vertices[int(index)] for index in face]
        append_unique(face_axes, seen_faces, (points[1] - points[0]).cross(points[2] - points[0]))
        for index, point in enumerate(points):
            append_unique(edge_axes, seen_edges, points[(index + 1) % len(points)] - point)
    return face_axes, edge_axes


def _convex_penetration_depth(first: Mapping[str, Any], second: Mapping[str, Any]) -> float:
    first_convex = first.get("convex")
    second_convex = second.get("convex")
    if not isinstance(first_convex, Mapping) or not isinstance(second_convex, Mapping):
        return _obb_penetration_depth(first, second)
    if _obb_penetration_depth(first, second) <= 0.0:
        return 0.0
    first_vertices = [Vector(tuple(float(component) for component in point)) for point in first_convex.get("vertices", [])]
    second_vertices = [Vector(tuple(float(component) for component in point)) for point in second_convex.get("vertices", [])]
    if not first_vertices or not second_vertices:
        return _obb_penetration_depth(first, second)
    first_faces, first_edges = _convex_axes(first_convex)
    second_faces, second_edges = _convex_axes(second_convex)
    axes = [*first_faces, *second_faces]
    seen = {tuple(round(float(component), 5) for component in axis) for axis in axes}
    for first_edge in first_edges:
        for second_edge in second_edges:
            cross = first_edge.cross(second_edge)
            if cross.length <= 1e-9:
                continue
            axis = cross.normalized()
            if next((component for component in axis if abs(component) > 1e-8), 1.0) < 0.0:
                axis.negate()
            key = tuple(round(float(component), 5) for component in axis)
            if key not in seen:
                seen.add(key)
                axes.append(axis)
    minimum_overlap = float("inf")
    for axis in axes:
        first_projection = [float(axis.dot(point)) for point in first_vertices]
        second_projection = [float(axis.dot(point)) for point in second_vertices]
        overlap = min(max(first_projection), max(second_projection)) - max(min(first_projection), min(second_projection))
        if overlap < 0.0:
            return 0.0
        minimum_overlap = min(minimum_overlap, overlap)
    return 0.0 if minimum_overlap == float("inf") else max(0.0, minimum_overlap)


def _obb_penetration_depth(first: Mapping[str, Any], second: Mapping[str, Any]) -> float:
    """Return the separating-axis overlap depth for two sampled OBBs."""

    first_obb = first.get("obb", first)
    second_obb = second.get("obb", second)
    axes_a = first_obb.get("axes")
    axes_b = second_obb.get("axes")
    half_a = first_obb.get("half_sizes")
    half_b = second_obb.get("half_sizes")
    center_a = first_obb.get("center", first.get("center"))
    center_b = second_obb.get("center", second.get("center"))
    if not all(isinstance(value, list) for value in (axes_a, axes_b, half_a, half_b, center_a, center_b)):
        return 0.0
    if len(axes_a) != 3 or len(axes_b) != 3 or len(half_a) != 3 or len(half_b) != 3:
        return 0.0
    delta = Vector(tuple(float(center_a[index]) - float(center_b[index]) for index in range(3)))
    vectors = [Vector(tuple(float(component) for component in axis)) for axis in axes_a]
    vectors.extend(Vector(tuple(float(component) for component in axis)) for axis in axes_b)
    for axis_a in axes_a:
        vector_a = Vector(tuple(float(component) for component in axis_a))
        for axis_b in axes_b:
            vector_b = Vector(tuple(float(component) for component in axis_b))
            cross = vector_a.cross(vector_b)
            if cross.length > 1e-9:
                vectors.append(cross)
    minimum_overlap = float("inf")
    for vector in vectors:
        if vector.length <= 1e-9:
            continue
        axis = vector.normalized()
        radius_a = sum(float(half_a[index]) * abs(axis.dot(Vector(tuple(float(component) for component in axes_a[index])))) for index in range(3))
        radius_b = sum(float(half_b[index]) * abs(axis.dot(Vector(tuple(float(component) for component in axes_b[index])))) for index in range(3))
        overlap = radius_a + radius_b - abs(axis.dot(delta))
        if overlap < 0.0:
            return 0.0
        minimum_overlap = min(minimum_overlap, overlap)
    return 0.0 if minimum_overlap == float("inf") else max(0.0, minimum_overlap)


def dynamic_static_obb_audit(
    objects: Sequence[Any],
    samples: Mapping[int, Mapping[str, Mapping[str, Any]]],
    *,
    contact_margin: float = 0.0,
    ignored_names: set[str] | None = None,
) -> dict[str, Any]:
    """Measure the worst sampled dynamic/passive OBB overlap after contact margin."""

    ignored = {str(name) for name in (ignored_names or set())}
    dynamic = [obj.name for obj in objects if getattr(getattr(obj, "rigid_body", None), "type", None) != "PASSIVE" and obj.name not in ignored]
    passive = [obj.name for obj in objects if getattr(getattr(obj, "rigid_body", None), "type", None) == "PASSIVE" and obj.name not in ignored]
    peak = 0.0
    raw_peak = 0.0
    peak_frame = None
    peak_pair = None
    for frame in sorted(samples):
        current = samples[frame]
        for dynamic_name in dynamic:
            first = current.get(dynamic_name)
            if first is None:
                continue
            for passive_name in passive:
                second = current.get(passive_name)
                if second is None:
                    continue
                raw = _obb_penetration_depth(first, second)
                measured = max(0.0, raw - float(contact_margin))
                if measured > peak:
                    peak = measured
                    raw_peak = raw
                    peak_frame = int(frame)
                    peak_pair = [dynamic_name, passive_name]
    return {
        "status": "pass",
        "max_penetration": float(peak),
        "raw_max_penetration": float(raw_peak),
        "contact_margin": float(contact_margin),
        "frame": peak_frame,
        "pair": peak_pair,
        "method": "sampled Blender evaluated OBB SAT dynamic/passive audit",
    }


def rigidbody_obb_audit(
    objects: Sequence[Any],
    samples: Mapping[int, Mapping[str, Mapping[str, Any]]],
    *,
    contact_margin: float = 0.0,
    ignored_names: set[str] | None = None,
) -> dict[str, Any]:
    """Measure sampled penetration for every pair containing an active body."""

    ignored = {str(name) for name in (ignored_names or set())}
    body_types = {
        obj.name: getattr(getattr(obj, "rigid_body", None), "type", None)
        for obj in objects
        if obj.name not in ignored
    }
    collision_collections = {
        obj.name: {
            index
            for index, enabled in enumerate(getattr(getattr(obj, "rigid_body", None), "collision_collections", (True,) * 20))
            if enabled
        }
        for obj in objects
        if obj.name in body_types
    }
    pairs = [
        (first, second)
        for first, second in combinations(sorted(body_types), 2)
        if body_types[first] != "PASSIVE" or body_types[second] != "PASSIVE"
        if collision_collections[first] & collision_collections[second]
    ]
    peak = 0.0
    raw_peak = 0.0
    peak_frame = None
    peak_pair = None
    for frame in sorted(samples):
        current = samples[frame]
        for first_name, second_name in pairs:
            first = current.get(first_name)
            second = current.get(second_name)
            if first is None or second is None:
                continue
            raw = _convex_penetration_depth(first, second)
            measured = max(0.0, raw - float(contact_margin))
            if measured > peak:
                peak = measured
                raw_peak = raw
                peak_frame = int(frame)
                peak_pair = [first_name, second_name]
    return {
        "status": "pass",
        "max_penetration": float(peak),
        "raw_max_penetration": float(raw_peak),
        "contact_margin": float(contact_margin),
        "frame": peak_frame,
        "pair": peak_pair,
        "pairs_checked": len(pairs),
        "method": "sampled Blender evaluated OBB broad phase plus convex-proxy SAT for collision-collection-compatible rigid-body pairs",
    }


def constraint_break_audit(
    matrices: Mapping[int, Mapping[str, Matrix]],
    constraints: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Infer fixed-constraint separation from changes in each body's relative transform."""

    if not matrices:
        return {"break_frames": {}, "constraints": []}
    first_frame = min(matrices)
    initial = matrices[first_frame]
    break_frames: dict[str, int | None] = {}
    reports: list[dict[str, Any]] = []
    identity = Matrix.Identity(4)
    for spec in constraints:
        name = str(spec["name"])
        first_name = str(spec["object1"])
        second_name = str(spec["object2"])
        translation_threshold = float(spec.get("monitor_translation", 0.03))
        rotation_threshold = float(spec.get("monitor_rotation", 0.05))
        break_frame = None
        peak_translation = 0.0
        peak_rotation = 0.0
        if first_name not in initial or second_name not in initial:
            break_frames[name] = None
            reports.append({"name": name, "object1": first_name, "object2": second_name, "break_frame": None, "missing": True})
            continue
        rest = initial[first_name].inverted_safe() @ initial[second_name]
        rest_inverse = rest.inverted_safe()
        for frame in sorted(matrices):
            current = matrices[frame]
            if first_name not in current or second_name not in current:
                continue
            relative = current[first_name].inverted_safe() @ current[second_name]
            error = rest_inverse @ relative
            translation = float(error.to_translation().length)
            rotation = _rotation_delta(identity, error)
            peak_translation = max(peak_translation, translation)
            peak_rotation = max(peak_rotation, rotation)
            if break_frame is None and (translation > translation_threshold or rotation > rotation_threshold):
                break_frame = int(frame)
        break_frames[name] = break_frame
        reports.append({
            "name": name,
            "object1": first_name,
            "object2": second_name,
            "break_frame": break_frame,
            "translation_threshold": translation_threshold,
            "rotation_threshold": rotation_threshold,
            "peak_translation": peak_translation,
            "peak_rotation": peak_rotation,
        })
    return {"break_frames": break_frames, "constraints": reports}


def evaluate_physics_event_chain(
    capture: SimulationCapture,
    physics: Mapping[str, Any],
    final_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate a contract physics block against a completed Blender capture."""

    return evaluate_event_chain(physics, capture.samples, final_report=final_report)


def write_physics_event_report(
    capture: SimulationCapture,
    physics: Mapping[str, Any],
    output_path: Path | str,
    final_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate and persist the deterministic event-chain report beside the bake evidence."""

    report = evaluate_physics_event_chain(capture, physics, final_report=final_report)
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def _outside_receiver(
    obj: Any,
    matrix: Matrix,
    bounds: tuple[float, float, float, float, float, float],
    margin: float,
) -> bool:
    minimum, maximum = _world_bounds(obj, matrix)
    receiver_min = bounds[:3]
    receiver_max = bounds[3:]
    return any(
        minimum[axis] < receiver_min[axis] - margin
        or maximum[axis] > receiver_max[axis] + margin
        for axis in range(3)
    )


def run_adaptive_simulation(
    scene: Any,
    objects: Sequence[Any],
    config: SettlementConfig,
) -> SimulationCapture:
    """Evaluate rigid bodies once, stop after a stable window, and retain transform samples."""

    config.validate()
    pieces = list(objects)
    names = [obj.name for obj in pieces]
    if not pieces or len(set(names)) != len(names):
        raise ValueError("objects must be a non-empty sequence with unique names")
    missing = [obj.name for obj in pieces if getattr(obj, "rigid_body", None) is None]
    if missing:
        raise ValueError(f"objects without rigid bodies: {missing}")
    if scene.rigidbody_world is None:
        raise RuntimeError("scene has no rigid-body world")

    scene.frame_start = config.frame_start
    scene.frame_end = config.max_frame
    scene.rigidbody_world.point_cache.frame_start = config.frame_start
    scene.rigidbody_world.point_cache.frame_end = config.max_frame

    matrices: dict[int, dict[str, Matrix]] = {}
    samples: dict[int, dict[str, dict[str, Any]]] = {}
    motion: dict[int, dict[str, Any]] = {}
    object_peaks = {
        name: {"translation": 0.0, "rotation": 0.0, "last_unstable_frame": None}
        for name in names
    }
    minimum = Vector((float("inf"),) * 3)
    maximum = Vector((float("-inf"),) * 3)
    stable_run = 0
    stable_window_start: int | None = None
    detection_frame: int | None = None
    target_end = config.max_frame

    scene.frame_set(config.frame_start)
    for frame in range(config.frame_start, config.max_frame + 1):
        scene.frame_set(frame)
        scene.view_layers[0].update()
        try:
            import bpy

            depsgraph = bpy.context.evaluated_depsgraph_get()
            evaluated = {obj.name: obj.evaluated_get(depsgraph) for obj in pieces}
        except (ImportError, AttributeError, RuntimeError):
            evaluated = {obj.name: obj for obj in pieces}
        current = {name: obj.matrix_world.copy() for name, obj in evaluated.items()}
        matrices[frame] = current
        samples[frame] = {}
        for obj in pieces:
            evaluated_obj = evaluated[obj.name]
            previous_matrix = matrices.get(frame - 1, {}).get(obj.name)
            rotation_delta = _rotation_delta(previous_matrix, current[obj.name]) if previous_matrix is not None else 0.0
            samples[frame][obj.name] = _obb_sample(evaluated_obj, current[obj.name], rotation_delta)
            low, high = _world_bounds(evaluated_obj, current[obj.name])
            for axis in range(3):
                minimum[axis] = min(minimum[axis], low[axis])
                maximum[axis] = max(maximum[axis], high[axis])

        if frame > config.frame_start:
            per_object = {}
            for name in names:
                translation = float(
                    (current[name].translation - matrices[frame - 1][name].translation).length
                )
                rotation = _rotation_delta(matrices[frame - 1][name], current[name])
                per_object[name] = {"translation": translation, "rotation": rotation}
                peak = object_peaks[name]
                peak["translation"] = max(float(peak["translation"]), translation)
                peak["rotation"] = max(float(peak["rotation"]), rotation)
                if (
                    translation > config.translation_epsilon
                    or rotation > config.rotation_epsilon
                ):
                    peak["last_unstable_frame"] = frame
            translation_owner = max(names, key=lambda name: per_object[name]["translation"])
            rotation_owner = max(names, key=lambda name: per_object[name]["rotation"])
            max_translation = per_object[translation_owner]["translation"]
            max_rotation = per_object[rotation_owner]["rotation"]
            motion[frame] = {
                "translation": max_translation,
                "translation_object": translation_owner,
                "rotation": max_rotation,
                "rotation_object": rotation_owner,
                "objects": per_object,
            }
            stable = (
                frame > config.activity_after_frame
                and max_translation <= config.translation_epsilon
                and max_rotation <= config.rotation_epsilon
            )
            stable_run = stable_run + 1 if stable else 0
            if detection_frame is None and stable_run >= config.consecutive_frames:
                detection_frame = frame
                stable_window_start = frame - config.consecutive_frames + 1
                target_end = min(config.max_frame, frame + config.hold_frames)

        if detection_frame is not None and frame >= target_end:
            break

    final_frame = max(matrices)
    scene.frame_end = final_frame
    scene.frame_set(final_frame)
    scene.view_layers[0].update()
    settled = detection_frame is not None
    tail_start = max(config.frame_start + 1, final_frame - config.consecutive_frames + 1)
    tail_frames = [frame for frame in range(tail_start, final_frame + 1) if frame in motion]
    tail_translation = max((motion[frame]["translation"] for frame in tail_frames), default=0.0)
    tail_rotation = max((motion[frame]["rotation"] for frame in tail_frames), default=0.0)
    tail_activity = []
    for name in names:
        translation = max(
            (motion[frame]["objects"][name]["translation"] for frame in tail_frames),
            default=0.0,
        )
        rotation = max(
            (motion[frame]["objects"][name]["rotation"] for frame in tail_frames),
            default=0.0,
        )
        tail_activity.append({"name": name, "translation": translation, "rotation": rotation})
    tail_activity.sort(key=lambda item: max(item["translation"], item["rotation"]), reverse=True)

    final_matrices = matrices[final_frame]
    kinematic_at_end = [obj.name for obj in pieces if bool(obj.rigid_body.kinematic)]
    potentially_unwoken = [
        obj.name
        for obj in pieces
        if bool(obj.rigid_body.use_start_deactivated)
        and float(object_peaks[obj.name]["translation"]) <= config.translation_epsilon
        and float(object_peaks[obj.name]["rotation"]) <= config.rotation_epsilon
    ]
    outside_receiver = []
    if config.receiver_bounds is not None:
        outside_receiver = [
            obj.name
            for obj in pieces
            if _outside_receiver(
                obj,
                final_matrices[obj.name],
                config.receiver_bounds,
                config.receiver_margin,
            )
        ]

    warnings = []
    if not settled:
        warnings.append("simulation reached max_frame before the stable window completed")
    if kinematic_at_end:
        warnings.append("export objects remain kinematic at the final frame")
    if potentially_unwoken:
        warnings.append("start-deactivated objects never moved; inspect support and wake-up behavior")
    if outside_receiver:
        warnings.append("objects finished outside the configured receiver bounds")

    report = {
        "settled": settled,
        "settled_frame": stable_window_start,
        "detection_frame": detection_frame,
        "frame_range": [config.frame_start, final_frame],
        "max_frame": config.max_frame,
        "frames_avoided": config.max_frame - final_frame,
        "consecutive_frames": config.consecutive_frames,
        "hold_frames": config.hold_frames,
        "translation_epsilon": config.translation_epsilon,
        "rotation_epsilon": config.rotation_epsilon,
        "tail_max_translation": tail_translation,
        "tail_max_rotation": tail_rotation,
        "tail_most_active": tail_activity[: config.diagnostics_limit],
        "object_peaks": object_peaks,
        "kinematic_at_end": kinematic_at_end,
        "potentially_unwoken": potentially_unwoken,
        "outside_receiver": outside_receiver,
        "warnings": warnings,
    }
    return SimulationCapture(
        matrices=matrices,
        animation_bounds=[*minimum, *maximum],
        report=report,
        samples=samples,
    )
