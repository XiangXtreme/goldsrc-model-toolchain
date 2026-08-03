"""Blender reconstruction for the independent SourceIO-derived MDL reader."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import bpy
from mathutils import Euler, Matrix, Vector

from ..core.errors import ToolchainError
from ..vendor.sourceio_goldsrc import read_mdl
from .action_import import audit_action_pose_matrices, create_action_from_local_frames


def _safe_name(value: str, fallback: str) -> str:
    cleaned = "".join(character if character.isalnum() or character in "_.-" else "_" for character in value)
    return cleaned.strip("._") or fallback


def _assert_isolated_scene() -> None:
    scene = bpy.context.scene
    if scene.rigidbody_world is not None:
        raise ToolchainError(
            "ROUNDTRIP",
            "roundtrip.bullet_world",
            "Round-trip import requires a scene with Bullet ownership already released",
        )
    owned = [obj.name for obj in bpy.data.objects if obj.rigid_body or obj.rigid_body_constraint]
    if owned:
        raise ToolchainError(
            "ROUNDTRIP",
            "roundtrip.bullet_objects",
            "Round-trip import found rigid-body data outside a world",
            {"objects": owned},
        )


def reset_roundtrip_scene() -> dict[str, list[str]]:
    """Reset the isolated readback scene without evaluating deleted Bullet data."""

    _assert_isolated_scene()
    removed: dict[str, list[str]] = {"objects": [], "collections": [], "actions": []}
    for obj in list(bpy.data.objects):
        removed["objects"].append(obj.name)
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in list(bpy.data.collections):
        removed["collections"].append(collection.name)
        bpy.data.collections.remove(collection)
    for action in list(bpy.data.actions):
        removed["actions"].append(action.name)
        bpy.data.actions.remove(action)
    for datablocks in (
        bpy.data.meshes, bpy.data.armatures, bpy.data.materials, bpy.data.images,
        bpy.data.cameras, bpy.data.lights,
    ):
        for datablock in list(datablocks):
            if datablock.users == 0:
                datablocks.remove(datablock)
    return removed


def _create_armature(mdl, scale: float, prefix: str):
    global_matrices = []
    for info in mdl.bones:
        local = Matrix.Translation(Vector(info.position) * scale) @ Euler(info.rotation).to_matrix().to_4x4()
        global_matrices.append(global_matrices[info.parent] @ local if info.parent >= 0 else local)
    armature_data = bpy.data.armatures.new(f"{prefix}_ARM_DATA")
    armature = bpy.data.objects.new(f"{prefix}_ARM", armature_data)
    armature.show_in_front = True
    armature["goldsrc_roundtrip"] = True
    bpy.context.scene.collection.objects.link(armature)
    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    for index, info in enumerate(mdl.bones):
        name = _safe_name(info.name, f"Bone_{index}")
        info.name = name
        bone = armature_data.edit_bones.new(name)
        bone.head = (0.0, 0.0, 0.0)
        bone.tail = (0.0, 1.0, 0.0)
        bone.matrix = global_matrices[index]
        bone.length = max(0.01, 0.25 * scale)
        if info.parent >= 0:
            bone.parent = armature_data.edit_bones[info.parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    return armature, global_matrices


def _create_materials(mdl, prefix: str):
    materials = []
    for index, texture in enumerate(mdl.textures):
        token = _safe_name(texture.name, f"texture_{index}.bmp")
        image = bpy.data.images.new(f"{prefix}_{token}_IMAGE", width=texture.width, height=texture.height, alpha=True)
        image.pixels.foreach_set(texture.pixels.ravel())
        image.pack()
        image.filepath_raw = token
        image["goldsrc_texture_flags"] = int(texture.flags)
        material = bpy.data.materials.new(f"{prefix}_{token}")
        material.use_nodes = True
        material["goldsrc_texture_token"] = token
        nodes = material.node_tree.nodes
        nodes.clear()
        output = nodes.new("ShaderNodeOutputMaterial")
        shader = nodes.new("ShaderNodeBsdfPrincipled")
        tex_node = nodes.new("ShaderNodeTexImage")
        tex_node.image = image
        material.node_tree.links.new(tex_node.outputs["Color"], shader.inputs["Base Color"])
        material.node_tree.links.new(tex_node.outputs["Alpha"], shader.inputs["Alpha"])
        material.node_tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])
        if texture.flags & 0x40:
            material.surface_render_method = "DITHERED"
        materials.append(material)
    return materials


def _command_triangles(vertices, fan: bool):
    if fan:
        for index in range(1, len(vertices) - 1):
            yield vertices[0], vertices[index + 1], vertices[index]
    else:
        for index in range(len(vertices) - 2):
            yield vertices[index], vertices[index + 2 - (index & 1)], vertices[index + 1 + (index & 1)]


def _create_model_object(mdl, bodypart, bodypart_index: int, model, model_index: int, armature, bone_matrices, materials, scale: float, prefix: str):
    if not len(model.vertices):
        return None
    faces = []
    face_uvs = []
    face_materials = []
    first_family = mdl.skin_families[0] if mdl.skin_families else list(range(len(materials)))
    for mesh in model.meshes:
        texture_index = first_family[mesh.skin_ref] if mesh.skin_ref < len(first_family) else mesh.skin_ref
        if not 0 <= texture_index < len(mdl.textures):
            raise ToolchainError(
                "ROUNDTRIP", "roundtrip.skin_ref", "MDL mesh references a missing texture",
                {"bodypart": bodypart.name, "model": model.name, "skin_ref": mesh.skin_ref},
            )
        texture = mdl.textures[texture_index]
        for command, fan in mesh.commands:
            for triangle in _command_triangles(command, fan):
                faces.append(tuple(vertex.vertex for vertex in triangle))
                face_uvs.append(tuple((vertex.uv[0] / texture.width, 1.0 - vertex.uv[1] / texture.height) for vertex in triangle))
                face_materials.append(texture_index)
    vertices = []
    for vertex_index, coordinate in enumerate(model.vertices):
        bone_index = int(model.bone_vertices[vertex_index])
        if not 0 <= bone_index < len(bone_matrices):
            raise ToolchainError(
                "ROUNDTRIP", "roundtrip.vertex_bone", "MDL vertex references a missing bone",
                {"model": model.name, "vertex": vertex_index, "bone": bone_index},
            )
        vertices.append(bone_matrices[bone_index] @ (Vector(coordinate) * scale))
    mesh_data = bpy.data.meshes.new(f"{prefix}_{_safe_name(model.name, str(model_index))}_MESH")
    mesh_data.from_pydata(vertices, [], faces)
    mesh_data.update()
    source_indices = mesh_data.attributes.new("goldsrc_source_vertex", "INT", "POINT")
    source_indices.data.foreach_set("value", list(range(len(vertices))))
    uv_layer = mesh_data.uv_layers.new(name="UVMap")
    for polygon, uvs, material_index in zip(mesh_data.polygons, face_uvs, face_materials):
        polygon.material_index = material_index
        polygon.use_smooth = True
        for loop_index, uv in zip(polygon.loop_indices, uvs):
            uv_layer.data[loop_index].uv = uv
    name = f"{prefix}_{_safe_name(bodypart.name, 'body')}_{model_index}_{_safe_name(model.name, 'model')}"
    obj = bpy.data.objects.new(name, mesh_data)
    bpy.context.scene.collection.objects.link(obj)
    for material in materials:
        obj.data.materials.append(material)
    obj.parent = armature
    modifier = obj.modifiers.new("GoldSrc Skeleton", "ARMATURE")
    modifier.object = armature
    for bone_index, bone in enumerate(mdl.bones):
        indices = [index for index, value in enumerate(model.bone_vertices) if int(value) == bone_index]
        if indices:
            group = obj.vertex_groups.new(name=bone.name)
            group.add(indices, 1.0, "REPLACE")
    obj["goldsrc_bodypart"] = bodypart.name
    obj["goldsrc_bodypart_index"] = bodypart_index
    obj["goldsrc_bodygroup_choice"] = model_index
    return obj


def _import_actions(mdl, armature, scale: float):
    if not mdl.sequences:
        return [], [], []
    if not armature.animation_data:
        armature.animation_data_create()
    actions = []
    external = []
    audits = []
    max_frame = 0
    first_fps = None
    animation_bones = [
        SimpleNamespace(index=index, name=bone.name, parent=bone.parent)
        for index, bone in enumerate(mdl.bones)
    ]
    for sequence, blends in zip(mdl.sequences, mdl.animations):
        if sequence.sequence_group:
            external.append({"name": sequence.name, "group": sequence.sequence_group})
            continue
        if not blends:
            continue
        first_fps = first_fps or sequence.fps
        max_frame = max(max_frame, sequence.frame_count - 1)
        for blend_index, decoded_frames in enumerate(blends):
            action_name = sequence.name if len(blends) == 1 else f"{sequence.name}_blend{blend_index}"
            frames = {index: poses for index, poses in enumerate(decoded_frames)}
            action = create_action_from_local_frames(
                armature, animation_bones, frames,
                name=action_name, scale=scale,
            )
            audits.append({
                "action": action.name,
                **audit_action_pose_matrices(
                    armature, action, animation_bones, frames, scale=scale,
                ),
            })
            actions.append(action)
    if actions:
        armature.animation_data.action = actions[0]
        armature.animation_data.action_slot = actions[0].slots[0]
        scene = bpy.context.scene
        scene.frame_start = 0
        scene.frame_end = max_frame
        scene.frame_set(0)
        if first_fps:
            scene.render.fps = max(1, round(first_fps))
            scene.render.fps_base = 1.0
    return actions, external, audits


def import_mdl(path: str | Path, *, scale: float = 1.0, reset_scene: bool = False) -> dict:
    mdl_path = Path(path).expanduser().resolve()
    if not mdl_path.is_file():
        raise ToolchainError("ROUNDTRIP", "roundtrip.missing_mdl", "MDL file is missing", {"path": str(mdl_path)})
    removed = reset_roundtrip_scene() if reset_scene else {"objects": [], "collections": [], "actions": []}
    try:
        mdl = read_mdl(mdl_path)
    except (OSError, ValueError) as exc:
        raise ToolchainError("ROUNDTRIP", "roundtrip.parse", str(exc), {"path": str(mdl_path)}) from exc
    prefix = _safe_name(Path(mdl.header.name).stem, mdl_path.stem)
    armature, bone_matrices = _create_armature(mdl, scale, prefix)
    materials = _create_materials(mdl, prefix)
    objects = []
    bodygroups = {}
    for bodypart_index, bodypart in enumerate(mdl.bodyparts):
        choices = []
        for model_index, model in enumerate(bodypart.models):
            obj = _create_model_object(
                mdl, bodypart, bodypart_index, model, model_index,
                armature, bone_matrices, materials, scale, prefix,
            )
            choices.append(obj.name if obj else None)
            if obj:
                objects.append(obj)
        bodygroups[bodypart.name] = choices
    actions, external_groups, action_audits = _import_actions(mdl, armature, scale)
    armature["goldsrc_skin_families"] = json.dumps(mdl.skin_families)
    armature["goldsrc_bodygroups"] = json.dumps(bodygroups)
    armature["goldsrc_external_sequence_groups"] = json.dumps(external_groups)
    return {
        "path": str(mdl_path),
        "armature": armature,
        "objects": objects,
        "materials": materials,
        "actions": actions,
        "bodygroups": bodygroups,
        "skin_families": mdl.skin_families,
        "external_sequence_groups": external_groups,
        "action_matrix_audits": action_audits,
        "reset": removed,
        "mdl": mdl,
    }
