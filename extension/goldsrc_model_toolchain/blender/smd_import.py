"""Small GoldSrc SMD importer exposed by the runtime API."""

from __future__ import annotations

from pathlib import Path

import bpy
from mathutils import Euler, Matrix, Vector

from ..core.errors import ToolchainError
from ..core.smd import read_smd, validate_smd


def _armature(document, scale: float, prefix: str):
    data = bpy.data.armatures.new(prefix + "_ARM_DATA")
    obj = bpy.data.objects.new(prefix + "_ARM", data)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    for bone in document.bones:
        edit = data.edit_bones.new(bone.name)
        edit.head = (0.0, 0.0, 0.0)
        edit.tail = (0.0, 0.0, max(0.01, 0.25 * scale))
        if bone.parent >= 0:
            edit.parent = data.edit_bones[bone.parent]
    bpy.ops.object.mode_set(mode="POSE")
    globals_by_id = {}
    first_frame = min(document.frames) if document.frames else 0
    poses = {pose.bone: pose for pose in document.frames.get(first_frame, [])}
    for bone in document.bones:
        pose = poses.get(bone.index)
        local = Matrix.Identity(4) if pose is None else Matrix.Translation(Vector(pose.position) * scale) @ Euler(pose.rotation).to_matrix().to_4x4()
        matrix = globals_by_id[bone.parent] @ local if bone.parent >= 0 else local
        globals_by_id[bone.index] = matrix
        obj.pose.bones[bone.name].matrix = matrix
    bpy.ops.pose.armature_apply(selected=False)
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
    action = bpy.data.actions.new(name)
    action.use_fake_user = True
    layer = action.layers.new("Layer")
    strip = layer.strips.new()
    slot = action.slots.new("OBJECT", armature.name)
    bag = strip.channelbags.new(slot)
    armature.animation_data_create()
    armature.animation_data.action = action
    armature.animation_data.action_slot = slot
    curves = {}
    for bone in document.bones:
        pose_bone = armature.pose.bones[bone.name]
        pose_bone.rotation_mode = "QUATERNION"
        escaped = bone.name.replace("\\", "\\\\").replace('"', '\\"')
        path = f'pose.bones["{escaped}"].'
        position = [bag.fcurves.new(path + "location", index=index) for index in range(3)]
        rotation = [bag.fcurves.new(path + "rotation_quaternion", index=index) for index in range(4)]
        for curve in position + rotation:
            curve.keyframe_points.add(len(document.frames))
        curves[bone.index] = position, rotation
    for point_index, frame in enumerate(sorted(document.frames)):
        globals_by_id = {}
        by_bone = {pose.bone: pose for pose in document.frames[frame]}
        for bone in document.bones:
            pose = by_bone[bone.index]
            local = Matrix.Translation(Vector(pose.position) * scale) @ Euler(pose.rotation).to_matrix().to_4x4()
            matrix = globals_by_id[bone.parent] @ local if bone.parent >= 0 else local
            globals_by_id[bone.index] = matrix
            pose_bone = armature.pose.bones[bone.name]
            pose_bone.matrix = matrix
            position, rotation = curves[bone.index]
            for index in range(3):
                position[index].keyframe_points[point_index].co = (frame, pose_bone.location[index])
            for index in range(4):
                rotation[index].keyframe_points[point_index].co = (frame, pose_bone.rotation_quaternion[index])
    for position, rotation in curves.values():
        for curve in position + rotation:
            curve.update()
    return action


def import_smd(path: str | Path, *, scale: float = 1.0, action_name: str | None = None) -> dict:
    resolved = Path(path).expanduser().resolve()
    try:
        document = read_smd(resolved)
    except (OSError, ValueError) as exc:
        raise ToolchainError("IMPORT", "smd.parse", str(exc), {"path": str(resolved)}) from exc
    issues = validate_smd(document, require_triangles=False)
    if issues:
        raise ToolchainError("IMPORT", "smd.invalid", "SMD failed validation", {"issues": issues})
    prefix = resolved.stem
    armature = _armature(document, scale, prefix)
    obj = _mesh(document, armature, resolved, scale, prefix)
    action = _action(document, armature, action_name or prefix, scale)
    return {
        "path": str(resolved), "armature": armature.name,
        "object": obj.name if obj else None, "action": action.name if action else None,
        "bones": len(document.bones), "triangles": len(document.triangles), "frames": len(document.frames),
    }
