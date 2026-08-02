"""Small GoldSrc SMD importer exposed by the runtime API."""

from __future__ import annotations

from pathlib import Path

import bpy
from mathutils import Euler, Matrix, Vector

from ..core.errors import ToolchainError
from ..core.smd import read_smd, validate_smd
from .action_import import audit_action_pose_matrices, create_action_from_local_frames


def _armature(document, scale: float, prefix: str):
    globals_by_id = {}
    first_frame = min(document.frames) if document.frames else 0
    poses = {pose.bone: pose for pose in document.frames.get(first_frame, [])}
    for bone in document.bones:
        pose = poses.get(bone.index)
        local = Matrix.Identity(4) if pose is None else Matrix.Translation(Vector(pose.position) * scale) @ Euler(pose.rotation).to_matrix().to_4x4()
        globals_by_id[bone.index] = globals_by_id[bone.parent] @ local if bone.parent >= 0 else local
    data = bpy.data.armatures.new(prefix + "_ARM_DATA")
    obj = bpy.data.objects.new(prefix + "_ARM", data)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    for bone in document.bones:
        edit = data.edit_bones.new(bone.name)
        edit.head = (0.0, 0.0, 0.0)
        edit.tail = (0.0, 1.0, 0.0)
        edit.matrix = globals_by_id[bone.index]
        edit.length = max(0.01, 0.25 * scale)
        if bone.parent >= 0:
            edit.parent = data.edit_bones[bone.parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    return obj


def _material(token: str, directory: Path):
    material = bpy.data.materials.get(token) or bpy.data.materials.new(token)
    material["goldsrc_texture_token"] = token
    image_path = directory / token
    if image_path.is_file():
        image = bpy.data.images.load(str(image_path), check_existing=True)
        material.use_nodes = True
        nodes = material.node_tree.nodes
        nodes.clear()
        output = nodes.new("ShaderNodeOutputMaterial")
        shader = nodes.new("ShaderNodeBsdfPrincipled")
        image_node = nodes.new("ShaderNodeTexImage")
        image_node.image = image
        material.node_tree.links.new(image_node.outputs["Color"], shader.inputs["Base Color"])
        material.node_tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    return material


def _mesh(document, armature, path: Path, scale: float, prefix: str):
    vertices = []
    faces = []
    uvs = []
    bones = []
    tokens = []
    for triangle in document.triangles:
        start = len(vertices)
        vertices.extend(Vector(vertex.position) * scale for vertex in triangle.vertices)
        faces.append((start, start + 1, start + 2))
        uvs.append(tuple(vertex.uv for vertex in triangle.vertices))
        bones.extend(vertex.bone for vertex in triangle.vertices)
        tokens.append(triangle.material)
    if not faces:
        return None
    mesh = bpy.data.meshes.new(prefix + "_MESH")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    uv_layer = mesh.uv_layers.new(name="UVMap")
    material_tokens = list(dict.fromkeys(tokens))
    materials = [_material(token, path.parent) for token in material_tokens]
    for material in materials:
        mesh.materials.append(material)
    for polygon, triangle_uvs, token in zip(mesh.polygons, uvs, tokens):
        polygon.material_index = material_tokens.index(token)
        for loop_index, uv in zip(polygon.loop_indices, triangle_uvs):
            uv_layer.data[loop_index].uv = uv
    obj = bpy.data.objects.new(prefix, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.parent = armature
    modifier = obj.modifiers.new("GoldSrc Skeleton", "ARMATURE")
    modifier.object = armature
    for bone in document.bones:
        indices = [index for index, value in enumerate(bones) if value == bone.index]
        if indices:
            group = obj.vertex_groups.new(name=bone.name)
            group.add(indices, 1.0, "REPLACE")
    return obj


def _action(document, armature, name: str, scale: float):
    if len(document.frames) <= 1:
        return None
    frames = _frame_channels(document)
    return create_action_from_local_frames(
        armature, document.bones, frames, name=name, scale=scale,
    )


def _frame_channels(document):
    frames = {}
    for frame, poses in document.frames.items():
        by_bone = {pose.bone: pose for pose in poses}
        frames[frame] = [
            (by_bone[bone.index].position, by_bone[bone.index].rotation)
            for bone in document.bones
        ]
    return frames


def _skeleton_signature(document):
    names = {bone.index: bone.name.casefold() for bone in document.bones}
    return [
        (bone.name.casefold(), names.get(bone.parent))
        for bone in document.bones
    ]


def _armature_signature(armature):
    return [
        (bone.name.casefold(), bone.parent.name.casefold() if bone.parent else None)
        for bone in armature.data.bones
    ]


def import_smd_animation(
    animation_smd: str | Path,
    *,
    reference_smd: str | Path | None = None,
    target_armature=None,
    scale: float = 1.0,
    action_name: str | None = None,
) -> dict:
    """Bind animation channels to an explicit reference rest pose or armature."""

    animation_path = Path(animation_smd).expanduser().resolve()
    try:
        animation = read_smd(animation_path)
    except (OSError, ValueError) as exc:
        raise ToolchainError("IMPORT", "smd.animation_parse", str(exc), {"path": str(animation_path)}) from exc
    issues = validate_smd(animation, require_triangles=False)
    if issues:
        raise ToolchainError("IMPORT", "smd.animation_invalid", "Animation SMD failed validation", {"issues": issues})
    if target_armature is not None and reference_smd is not None:
        raise ToolchainError(
            "IMPORT", "smd.animation_target", "Choose target_armature or reference_smd, not both",
        )
    reference_path = None
    created_armature = False
    reference_object = None
    if target_armature is not None:
        armature = bpy.data.objects.get(target_armature) if isinstance(target_armature, str) else target_armature
        if armature is None or armature.type != "ARMATURE":
            raise ToolchainError(
                "IMPORT", "smd.animation_target", "target_armature must resolve to an Armature object",
                {"target_armature": str(target_armature)},
            )
        expected_signature = _armature_signature(armature)
    elif reference_smd is not None:
        reference_path = Path(reference_smd).expanduser().resolve()
        try:
            reference = read_smd(reference_path)
        except (OSError, ValueError) as exc:
            raise ToolchainError("IMPORT", "smd.reference_parse", str(exc), {"path": str(reference_path)}) from exc
        reference_issues = validate_smd(reference, require_triangles=False)
        if reference_issues:
            raise ToolchainError("IMPORT", "smd.reference_invalid", "Reference SMD failed validation", {"issues": reference_issues})
        expected_signature = _skeleton_signature(reference)
        prefix = reference_path.stem
        armature = _armature(reference, scale, prefix)
        reference_object = _mesh(reference, armature, reference_path, scale, prefix)
        created_armature = True
    else:
        raise ToolchainError(
            "IMPORT", "smd.animation_reference_required",
            "Animation SMD has no bind pose; provide reference_smd or target_armature",
            {"animation_smd": str(animation_path)},
        )
    actual_signature = _skeleton_signature(animation)
    if actual_signature != expected_signature:
        if created_armature:
            bpy.data.objects.remove(armature, do_unlink=True)
        raise ToolchainError(
            "IMPORT", "smd.animation_skeleton",
            "Animation nodes do not match the target rest skeleton and parent order",
            {"expected": expected_signature, "actual": actual_signature},
        )
    frames = _frame_channels(animation)
    action = create_action_from_local_frames(
        armature, animation.bones, frames,
        name=action_name or animation_path.stem, scale=scale,
    )
    audit = audit_action_pose_matrices(
        armature, action, animation.bones, frames, scale=scale,
    )
    return {
        "animation_smd": str(animation_path),
        "reference_smd": str(reference_path) if reference_path else None,
        "armature": armature.name,
        "object": reference_object.name if reference_object else None,
        "action": action.name,
        "bones": len(animation.bones),
        "frames": len(animation.frames),
        "matrix_audit": audit,
    }


def import_smd(path: str | Path, *, scale: float = 1.0, action_name: str | None = None) -> dict:
    resolved = Path(path).expanduser().resolve()
    try:
        document = read_smd(resolved)
    except (OSError, ValueError) as exc:
        raise ToolchainError("IMPORT", "smd.parse", str(exc), {"path": str(resolved)}) from exc
    issues = validate_smd(document, require_triangles=False)
    if issues:
        raise ToolchainError("IMPORT", "smd.invalid", "SMD failed validation", {"issues": issues})
    if len(document.frames) > 1 and not document.triangles:
        raise ToolchainError(
            "IMPORT", "smd.animation_reference_required",
            "Animation-only SMD has no bind pose; use import_smd_animation with reference_smd or target_armature",
            {"path": str(resolved)},
        )
    prefix = resolved.stem
    armature = _armature(document, scale, prefix)
    obj = _mesh(document, armature, resolved, scale, prefix)
    action = _action(document, armature, action_name or prefix, scale)
    return {
        "path": str(resolved), "armature": armature.name,
        "object": obj.name if obj else None, "action": action.name if action else None,
        "bones": len(document.bones), "triangles": len(document.triangles), "frames": len(document.frames),
    }
