"""Analyze and non-destructively prepare one static GoldSrc MDL export."""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from pathlib import Path
from typing import Any
from uuid import uuid4

import bpy
from mathutils import Matrix, Vector

from ..core.errors import ToolchainError
from ..core.material_mapping import (
    STATIC_MATERIAL_AUDIT_FIELD,
    STATIC_MATERIAL_AUDIT_PROPERTY,
    aggregate_token_triangles,
    inspect_mesh_material_usage,
    material_key,
    original_material,
)
from ..core.model_contract import TEXTURE_MODES, validate_contract
from ..core.paths import resolve_artifact_root
from ..core.reporting import write_json


UV_STRATEGIES = {"existing", "smart_project"}
ORIGIN_STRATEGIES = {
    "source_origin", "world_origin", "bounds_center", "bounds_base_center",
}
BAKE_MODES = {"image_passthrough", "color_only", "unlit_color"}
ALPHA_MODES = {"masked", "alpha", "additive"}
PREPARED_MARKER = "goldsrc_static_prepared"
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


def _short_name(value: str, fallback: str, *, maximum: int = 40) -> str:
    name = _SAFE_NAME.sub("_", str(value)).strip("._-") or fallback
    return name[:maximum]


def _unique_id_name(collection, desired: str) -> str:
    if collection.get(desired) is None:
        return desired
    index = 2
    while collection.get(f"{desired}_{index}") is not None:
        index += 1
    return f"{desired}_{index}"


def _resolve_mesh(object_name: str | None):
    if object_name is None:
        obj = bpy.context.view_layer.objects.active
        if obj is None:
            raise ToolchainError(
                "ANALYZE", "static.active_object", "No active mesh object is available",
                {},
            )
    else:
        obj = bpy.data.objects.get(str(object_name))
        if obj is None:
            raise ToolchainError(
                "ANALYZE", "static.object_missing", "The requested mesh object is missing",
                {"object": str(object_name)},
            )
    if obj.type != "MESH":
        raise ToolchainError(
            "ANALYZE", "static.object_type", "Static analysis requires one mesh object",
            {"object": obj.name, "type": obj.type},
        )
    if obj.name not in bpy.context.scene.objects:
        raise ToolchainError(
            "ANALYZE", "static.object_scene", "The requested mesh is not in the active Scene",
            {"object": obj.name, "scene": bpy.context.scene.name},
        )
    return obj


def _rounded(values) -> list[float]:
    return [round(float(value), 9) for value in values]


def _image_fingerprint(image) -> dict[str, Any]:
    path = Path(bpy.path.abspath(image.filepath)).expanduser() if image.filepath else None
    stat = None
    if path and path.is_file():
        info = path.stat()
        stat = [info.st_size, info.st_mtime_ns]
    pixels = []
    try:
        count = len(image.pixels)
        if count:
            for index in sorted({0, count // 7, count // 3, count // 2, count - 1}):
                pixels.append(round(float(image.pixels[index]), 7))
    except (RuntimeError, ValueError):
        pixels = []
    return {
        "name": image.name,
        "source": image.source,
        "size": [int(value) for value in image.size],
        "filepath": str(path) if path else None,
        "file_stat": stat,
        "dirty": bool(image.is_dirty),
        "samples": pixels,
    }


def _socket_default(socket):
    if not hasattr(socket, "default_value"):
        return None
    value = socket.default_value
    if isinstance(value, (int, float, bool, str)):
        return value
    try:
        return _rounded(value)
    except (TypeError, ValueError):
        return str(value)


def _material_fingerprint(material) -> dict[str, Any] | None:
    if material is None:
        return None
    result: dict[str, Any] = {
        "name": material.name,
        "token": material.get("goldsrc_texture_token"),
        "diffuse_color": _rounded(material.diffuse_color),
        "use_nodes": bool(material.use_nodes),
        "surface_render_method": getattr(material, "surface_render_method", None),
        "blend_method": getattr(material, "blend_method", None),
    }
    if not material.use_nodes or material.node_tree is None:
        return result
    nodes = []
    images = []
    for node in material.node_tree.nodes:
        nodes.append({
            "name": node.name,
            "type": node.bl_idname,
            "mute": bool(node.mute),
            "inputs": [(item.identifier, _socket_default(item)) for item in node.inputs],
        })
        image = getattr(node, "image", None)
        if image is not None:
            images.append(_image_fingerprint(image))
    result["nodes"] = sorted(nodes, key=lambda item: (item["type"], item["name"]))
    result["links"] = sorted(
        (
            link.from_node.name, link.from_socket.identifier,
            link.to_node.name, link.to_socket.identifier,
        )
        for link in material.node_tree.links
    )
    result["images"] = sorted(images, key=lambda item: item["name"])
    return result


def _uv_facts(mesh) -> dict[str, Any]:
    mesh.calc_loop_triangles()
    layers = []
    for layer in mesh.uv_layers:
        coordinates = [(float(item.uv.x), float(item.uv.y)) for item in layer.data]
        finite = all(math.isfinite(value) for pair in coordinates for value in pair)
        degenerate = 0
        checked = 0
        if finite:
            for triangle in mesh.loop_triangles:
                uvs = [coordinates[index] for index in triangle.loops]
                area = abs(
                    (uvs[1][0] - uvs[0][0]) * (uvs[2][1] - uvs[0][1])
                    - (uvs[2][0] - uvs[0][0]) * (uvs[1][1] - uvs[0][1])
                ) * 0.5
                positions = [mesh.vertices[mesh.loops[index].vertex_index].co for index in triangle.loops]
                if (positions[1] - positions[0]).cross(positions[2] - positions[0]).length <= 1.0e-12:
                    continue
                checked += 1
                if area <= 1.0e-12:
                    degenerate += 1
        bounds = None
        if coordinates and finite:
            bounds = {
                "min": [min(value[0] for value in coordinates), min(value[1] for value in coordinates)],
                "max": [max(value[0] for value in coordinates), max(value[1] for value in coordinates)],
            }
        layers.append({
            "name": layer.name,
            "active_render": bool(layer.active_render),
            "loops": len(coordinates),
            "finite": finite,
            "nondegenerate_triangles": checked - degenerate,
            "degenerate_triangles": degenerate,
            "bounds": bounds,
            "valid": finite and checked > 0 and degenerate == 0,
        })
    active = mesh.uv_layers.active
    return {
        "active": active.name if active else None,
        "active_render": next((item["name"] for item in layers if item["active_render"]), None),
        "layers": layers,
    }


def _material_transparency(material) -> bool:
    if material is None:
        return False
    if getattr(material, "surface_render_method", "DITHERED") != "DITHERED":
        return True
    blend = getattr(material, "blend_method", "HASHED")
    if blend not in {"OPAQUE", "HASHED"}:
        return True
    if float(material.diffuse_color[3]) < 0.999999:
        return True
    if not material.use_nodes or material.node_tree is None:
        return False
    for node in material.node_tree.nodes:
        if node.type == "BSDF_TRANSPARENT":
            return True
        if node.type == "BSDF_PRINCIPLED":
            alpha = node.inputs.get("Alpha")
            if alpha and (alpha.is_linked or float(alpha.default_value) < 0.999999):
                return True
    return False


def _surface_nodes(material) -> tuple[list[Any], list[str]]:
    if material is None or not material.use_nodes or material.node_tree is None:
        return [], ["material_without_nodes"]
    node_groups = [
        node.bl_idname for node in material.node_tree.nodes
        if node.type == "GROUP"
    ]
    outputs = [node for node in material.node_tree.nodes if node.type == "OUTPUT_MATERIAL" and node.is_active_output]
    if not outputs:
        return [], sorted(set(["missing_active_material_output", *node_groups]))
    pending = []
    unsupported = list(node_groups)
    visited = set()
    for output in outputs:
        surface = output.inputs.get("Surface")
        if surface is None or not surface.is_linked:
            unsupported.append("unlinked_surface")
        else:
            pending.extend(link.from_node for link in surface.links)
        volume = output.inputs.get("Volume")
        displacement = output.inputs.get("Displacement")
        if volume and volume.is_linked:
            unsupported.append("volume_surface")
        if displacement and displacement.is_linked:
            unsupported.append("material_displacement")
    allowed = {"BSDF_PRINCIPLED", "BSDF_DIFFUSE", "EMISSION", "MIX_SHADER", "ADD_SHADER"}
    while pending:
        node = pending.pop()
        pointer = node.as_pointer()
        if pointer in visited:
            continue
        visited.add(pointer)
        if node.type not in allowed:
            unsupported.append(node.bl_idname)
            continue
        if node.type in {"MIX_SHADER", "ADD_SHADER"}:
            for socket in node.inputs:
                if socket.type == "SHADER" and socket.is_linked:
                    pending.extend(link.from_node for link in socket.links)
    return [node for node in material.node_tree.nodes if node.as_pointer() in visited], sorted(set(unsupported))


def _alpha_bake_unsupported(material) -> list[str]:
    if material is None or not _material_transparency(material):
        return []
    if not material.use_nodes or material.node_tree is None:
        return []
    unsupported = [
        node.bl_idname for node in material.node_tree.nodes
        if node.type == "GROUP"
    ]
    outputs = [
        node for node in material.node_tree.nodes
        if node.type == "OUTPUT_MATERIAL" and node.is_active_output
    ]
    if not outputs:
        unsupported.append("missing_active_material_output")
        return sorted(set(unsupported))
    pending = []
    for output in outputs:
        surface = output.inputs.get("Surface")
        if surface is None or not surface.is_linked:
            unsupported.append("unlinked_surface")
        else:
            pending.extend(link.from_node for link in surface.links)
    allowed = {
        "BSDF_PRINCIPLED", "BSDF_DIFFUSE", "EMISSION",
        "BSDF_TRANSPARENT", "MIX_SHADER",
    }
    visited = set()
    while pending:
        node = pending.pop()
        pointer = node.as_pointer()
        if pointer in visited:
            continue
        visited.add(pointer)
        if node.type not in allowed:
            unsupported.append(node.bl_idname)
            continue
        if node.type == "MIX_SHADER":
            shader_inputs = [socket for socket in node.inputs if socket.type == "SHADER"]
            if len(shader_inputs) != 2 or any(not socket.is_linked for socket in shader_inputs):
                unsupported.append("unlinked_mix_shader")
                continue
            for socket in shader_inputs:
                pending.extend(link.from_node for link in socket.links)
    return sorted(set(unsupported))


def _material_facts(material) -> dict[str, Any]:
    surface_nodes, unsupported = _surface_nodes(material)
    images = []
    if material and material.use_nodes and material.node_tree:
        images = [
            _image_fingerprint(node.image)
            for node in material.node_tree.nodes
            if node.type == "TEX_IMAGE" and node.image is not None
        ]
    return {
        "name": material.name if material else None,
        "goldsrc_texture_token": material.get("goldsrc_texture_token") if material else None,
        "transparent": _material_transparency(material),
        "surface_closures": sorted({node.bl_idname for node in surface_nodes}),
        "unsupported_unlit": unsupported,
        "unsupported_alpha_bake": _alpha_bake_unsupported(material),
        "images": images,
    }


def _mesh_geometry_signature(mesh) -> str:
    """Hash ordered geometry so a second dependency-graph evaluation cannot be remapped blindly."""

    digest = hashlib.sha256()
    digest.update(struct.pack(
        "<QQQ", len(mesh.vertices), len(mesh.loops), len(mesh.polygons),
    ))
    for vertex in mesh.vertices:
        digest.update(struct.pack("<3d", *(float(value) for value in vertex.co)))
    for polygon in mesh.polygons:
        vertices = tuple(int(value) for value in polygon.vertices)
        digest.update(struct.pack("<Q", len(vertices)))
        if vertices:
            digest.update(struct.pack(f"<{len(vertices)}Q", *vertices))
    return digest.hexdigest()


def _ensure_material_slots(mesh, obj) -> None:
    if len(mesh.materials):
        return
    for slot in obj.material_slots:
        mesh.materials.append(slot.material)


def _weight_facts(obj, mesh) -> dict[str, Any]:
    group_names = {group.index: group.name for group in obj.vertex_groups}
    unweighted = 0
    multiweighted = 0
    groups = set()
    for vertex in mesh.vertices:
        influences = [item for item in vertex.groups if item.weight > 0.000001]
        if not influences:
            unweighted += 1
        elif len(influences) != 1 or not math.isclose(influences[0].weight, 1.0, abs_tol=0.0001):
            multiweighted += 1
        groups.update(group_names.get(item.group, str(item.group)) for item in influences)
    return {
        "groups": sorted(groups),
        "unweighted": unweighted,
        "multiweighted": multiweighted,
    }


def _analyze_object(obj) -> tuple[dict[str, Any], str]:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    if mesh is None:
        raise ToolchainError(
            "ANALYZE", "static.evaluated_mesh", "Could not evaluate the static mesh",
            {"object": obj.name},
        )
    try:
        mesh.calc_loop_triangles()
        _ensure_material_slots(mesh, obj)
        usage = inspect_mesh_material_usage(mesh)
        materials = list(usage.materials)
        uv = _uv_facts(mesh)
        modifiers = [
            {
                "name": modifier.name,
                "type": modifier.type,
                "show_viewport": bool(modifier.show_viewport),
                "show_render": bool(modifier.show_render),
            }
            for modifier in obj.modifiers
        ]
        armatures = sorted({
            modifier.object.name
            for modifier in obj.modifiers
            if modifier.type == "ARMATURE" and modifier.object is not None
        } | ({obj.parent.name} if obj.parent and obj.parent.type == "ARMATURE" else set()))
        material_facts = [
            {
                **_material_facts(material),
                "slot": distribution["slot"],
                "faces": distribution["faces"],
                "triangles": distribution["triangles"],
                "used": distribution["used"],
            }
            for material, distribution in zip(materials, usage.distribution)
        ]
        facts = {
            "object": obj.name,
            "scene": bpy.context.scene.name,
            "source_vertices": len(obj.data.vertices),
            "source_polygons": len(obj.data.polygons),
            "evaluated_vertices": len(mesh.vertices),
            "evaluated_polygons": len(mesh.polygons),
            "evaluated_triangles": len(mesh.loop_triangles),
            "modifiers": modifiers,
            "uv": uv,
            "materials": material_facts,
            "material_distribution": list(usage.distribution),
            "invalid_material_indices": list(usage.invalid_indices),
            "transform": {
                "location": _rounded(obj.location),
                "rotation_euler": _rounded(obj.rotation_euler),
                "scale": _rounded(obj.scale),
                "matrix_world": _rounded(value for row in obj.matrix_world for value in row),
            },
            "weights": _weight_facts(evaluated, mesh),
            "armatures": armatures,
            "actions": sorted(
                {action.name for action in bpy.data.actions}
            ),
        }
        fingerprint_payload = {
            "file": bpy.data.filepath,
            "object_pointer": obj.as_pointer(),
            "facts": facts,
            "vertices": [_rounded(vertex.co) for vertex in mesh.vertices],
            "polygons": [list(polygon.vertices) for polygon in mesh.polygons],
            "polygon_material_indices": list(usage.polygon_indices),
            "uv_values": {
                layer.name: [_rounded(item.uv) for item in layer.data]
                for layer in mesh.uv_layers
            },
            "materials": [_material_fingerprint(material) for material in materials],
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        ).hexdigest()
        return facts, fingerprint
    finally:
        evaluated.to_mesh_clear()


def analyze_selected_static(object_name: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    obj = _resolve_mesh(object_name)
    facts, fingerprint = _analyze_object(obj)
    analysis_id = uuid4().hex
    valid_uvs = [item["name"] for item in facts["uv"]["layers"] if item["valid"]]
    used_materials = [item for item in facts["materials"] if item["used"]]
    transparent = [item["name"] for item in used_materials if item["transparent"]]
    pending = [
        {
            "parameter": "uv_strategy",
            "options": sorted(UV_STRATEGIES),
            "valid_existing_layers": valid_uvs,
        },
        {"parameter": "origin_strategy", "options": sorted(ORIGIN_STRATEGIES)},
        {"parameter": "bake_mode", "options": sorted(BAKE_MODES)},
    ]
    if transparent:
        pending.append({
            "parameter": "goldsrc_modes",
            "reason": "transparent_material_semantics",
            "materials": transparent,
            "options": sorted(ALPHA_MODES),
        })
    summary = {
        "object": obj.name,
        "evaluated_vertices": facts["evaluated_vertices"],
        "evaluated_triangles": facts["evaluated_triangles"],
        "modifiers": [item["type"] for item in facts["modifiers"]],
        "uv_layers": [item["name"] for item in facts["uv"]["layers"]],
        "valid_uv_layers": valid_uvs,
        "materials": [item["name"] for item in used_materials],
        "evaluated_material_slots": [item["name"] for item in facts["materials"]],
        "evaluated_material_distribution": facts["material_distribution"],
        "transparent_materials": transparent,
        "armatures": facts["armatures"],
        "weighted": facts["weights"]["unweighted"] == 0 and facts["weights"]["multiweighted"] == 0,
        "nonzero_location": any(abs(value) > 1.0e-9 for value in facts["transform"]["location"]),
    }
    return (
        {
            "status": "pass",
            "analysis_id": analysis_id,
            "summary": summary,
            "pending_decisions": pending,
        },
        {
            "analysis_id": analysis_id,
            "object_name": obj.name,
            "fingerprint": fingerprint,
            "facts": facts,
        },
    )


def _decision(parameter: str, options, reason: str, **details) -> dict[str, Any]:
    return {
        "parameter": parameter,
        "options": list(options),
        "reason": reason,
        **details,
    }


def _needs_decision(analysis_id: str, decisions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "needs_decision",
        "analysis_id": analysis_id,
        "decisions": decisions,
    }


def _validate_texture_size(texture_size: Any) -> int:
    if not isinstance(texture_size, int) or isinstance(texture_size, bool):
        raise ToolchainError(
            "PREPARE", "static.texture_size", "texture_size must be an integer",
            {"texture_size": texture_size},
        )
    if texture_size <= 0 or texture_size > 4096 or texture_size % 16:
        raise ToolchainError(
            "PREPARE", "static.texture_size",
            "texture_size must be a multiple of 16 within 16..4096",
            {"texture_size": texture_size},
        )
    if texture_size > 512 and texture_size % 512:
        raise ToolchainError(
            "PREPARE", "static.large_texture_size",
            "logical textures larger than 512 must divide into 512px tiles",
            {"texture_size": texture_size},
        )
    return texture_size


def _modes_for(material, token: str, modes) -> list[str]:
    if modes is None:
        return []
    selected = modes
    if isinstance(modes, dict):
        selected = modes.get(material.name, modes.get(token, modes.get("default", [])))
    if not isinstance(selected, (list, tuple, set)):
        raise ToolchainError(
            "PREPARE", "static.goldsrc_modes", "goldsrc_modes must be a list or material mapping",
            {"material": material.name, "value": selected},
        )
    result = list(dict.fromkeys(str(value) for value in selected))
    unknown = sorted(set(result) - TEXTURE_MODES)
    if unknown:
        raise ToolchainError(
            "PREPARE", "static.goldsrc_modes", "goldsrc_modes contains unsupported values",
            {"material": material.name, "unsupported": unknown},
        )
    return result


def _capture_context() -> dict[str, Any]:
    view_layer = bpy.context.view_layer
    active = view_layer.objects.active
    return {
        "active": active,
        "selected": [obj for obj in view_layer.objects if obj.select_get()],
        "mode": active.mode if active else "OBJECT",
        "frame": bpy.context.scene.frame_current,
        "frame_start": bpy.context.scene.frame_start,
        "frame_end": bpy.context.scene.frame_end,
        "filepath": bpy.data.filepath,
    }


def _object_mode() -> None:
    active = bpy.context.view_layer.objects.active
    if active is not None and active.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


def _restore_context(state: dict[str, Any], *, restore_timeline: bool) -> None:
    view_layer = bpy.context.view_layer
    _object_mode()
    for obj in view_layer.objects:
        obj.select_set(False)
    active = state["active"]
    if active is not None and active.name in view_layer.objects:
        active.select_set(True)
        view_layer.objects.active = active
        for obj in state["selected"]:
            if obj.name in view_layer.objects:
                obj.select_set(True)
        mode = state["mode"]
        if mode.startswith("EDIT"):
            bpy.ops.object.mode_set(mode="EDIT")
        elif mode in {"POSE", "SCULPT", "VERTEX_PAINT", "WEIGHT_PAINT", "TEXTURE_PAINT"}:
            try:
                bpy.ops.object.mode_set(mode=mode)
            except RuntimeError:
                pass
    else:
        view_layer.objects.active = None
    if restore_timeline:
        scene = bpy.context.scene
        scene.frame_start = int(state["frame_start"])
        scene.frame_end = int(state["frame_end"])
        scene.frame_set(int(state["frame"]))


def capture_static_export_session() -> dict[str, Any]:
    """Capture author context that the one-call product API must restore."""

    return _capture_context()


def restore_static_export_session(state: dict[str, Any]) -> None:
    """Restore author context after preparation and validation have completed."""

    _restore_context(state, restore_timeline=True)


class _CreatedData:
    def __init__(self) -> None:
        self.collections = []
        self.objects = []
        self.meshes = []
        self.armatures = []
        self.materials = []
        self.images = []
        self.actions = []
        self.files: list[Path] = []

    def cleanup(self) -> None:
        _object_mode()
        for obj in reversed(self.objects):
            if obj.name in bpy.data.objects:
                bpy.data.objects.remove(obj, do_unlink=True)
        for action in reversed(self.actions):
            if action.name in bpy.data.actions:
                bpy.data.actions.remove(action)
        for datablocks, values in (
            (bpy.data.meshes, self.meshes),
            (bpy.data.armatures, self.armatures),
            (bpy.data.materials, self.materials),
            (bpy.data.images, self.images),
            (bpy.data.collections, self.collections),
        ):
            for value in reversed(values):
                if value.name in datablocks:
                    datablocks.remove(value)
        for path in reversed(self.files):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _capture_evaluated_surface(source) -> dict[str, Any]:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = source.evaluated_get(depsgraph)
    evaluated_mesh = evaluated.to_mesh(
        preserve_all_data_layers=True, depsgraph=depsgraph,
    )
    if evaluated_mesh is None:
        raise ToolchainError(
            "PREPARE", "static.evaluated_mesh", "Could not evaluate the source mesh for freezing",
            {"object": source.name},
        )
    try:
        _ensure_material_slots(evaluated_mesh, evaluated)
        usage = inspect_mesh_material_usage(evaluated_mesh)
        if usage.invalid_indices:
            raise ToolchainError(
                "PREPARE", "static.evaluated_material_index",
                "Evaluated polygons reference material slots that do not exist",
                {
                    "object": source.name,
                    "invalid_indices": list(usage.invalid_indices),
                    "material_slots": len(usage.materials),
                },
            )
        used_slots = [item["slot"] for item in usage.distribution if item["used"]]
        missing = [
            slot for slot in used_slots
            if original_material(usage.materials[slot]) is None
        ]
        if missing:
            raise ToolchainError(
                "PREPARE", "static.evaluated_material_missing",
                "Every used evaluated material slot must contain an explicit material",
                {"object": source.name, "slots": missing},
            )
        return {
            "geometry_sha256": _mesh_geometry_signature(evaluated_mesh),
            "vertices": len(evaluated_mesh.vertices),
            "polygons": len(evaluated_mesh.polygons),
            "triangles": usage.triangles,
            "materials": tuple(original_material(item) for item in usage.materials),
            "polygon_material_indices": usage.polygon_indices,
            "distribution": list(usage.distribution),
            "used_slots": used_slots,
        }
    finally:
        evaluated.to_mesh_clear()


def _freeze_mesh(source, collection, origin_strategy: str, created: _CreatedData, stem: str):
    snapshot = _capture_evaluated_surface(source)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = source.evaluated_get(depsgraph)
    mesh = bpy.data.meshes.new_from_object(
        evaluated, preserve_all_data_layers=True, depsgraph=depsgraph,
    )
    if mesh is None:
        raise ToolchainError(
            "PREPARE", "static.freeze_mesh", "Blender did not create a frozen evaluated mesh",
            {"object": source.name},
        )
    created.meshes.append(mesh)
    mesh.name = _unique_id_name(bpy.data.meshes, f"{stem}_GoldSrc_MESH")
    frozen_signature = _mesh_geometry_signature(mesh)
    if frozen_signature != snapshot["geometry_sha256"]:
        raise ToolchainError(
            "PREPARE", "static.evaluated_topology_changed",
            "The evaluated mesh changed while Blender was freezing it",
            {
                "object": source.name,
                "captured_geometry_sha256": snapshot["geometry_sha256"],
                "frozen_geometry_sha256": frozen_signature,
                "captured": {
                    "vertices": snapshot["vertices"],
                    "polygons": snapshot["polygons"],
                },
                "frozen": {
                    "vertices": len(mesh.vertices),
                    "polygons": len(mesh.polygons),
                },
            },
        )

    old_to_new: dict[int, int] = {}
    compact_materials = []
    compact_keys = {}
    for old_index in snapshot["used_slots"]:
        material = snapshot["materials"][old_index]
        key = material_key(material)
        if key not in compact_keys:
            compact_keys[key] = len(compact_materials)
            compact_materials.append(material)
        old_to_new[old_index] = compact_keys[key]
    mesh.materials.clear()
    for material in compact_materials:
        mesh.materials.append(material)
    for polygon, old_index in zip(mesh.polygons, snapshot["polygon_material_indices"]):
        polygon.material_index = old_to_new[old_index]
    mesh.update()

    remapped = inspect_mesh_material_usage(mesh)
    expected_counts: dict[int, dict[str, int]] = {}
    for item in snapshot["distribution"]:
        if not item["used"]:
            continue
        new_index = old_to_new[item["slot"]]
        counts = expected_counts.setdefault(new_index, {"faces": 0, "triangles": 0})
        counts["faces"] += int(item["faces"])
        counts["triangles"] += int(item["triangles"])
    actual_counts = {
        item["slot"]: {"faces": int(item["faces"]), "triangles": int(item["triangles"])}
        for item in remapped.distribution
    }
    if remapped.invalid_indices or expected_counts != actual_counts:
        raise ToolchainError(
            "PREPARE", "static.evaluated_material_mapping",
            "Frozen material indices do not preserve the evaluated surface distribution",
            {
                "object": source.name,
                "invalid_indices": list(remapped.invalid_indices),
                "expected": expected_counts,
                "actual": actual_counts,
            },
        )

    obj = bpy.data.objects.new(
        _unique_id_name(bpy.data.objects, f"{stem}_GoldSrc"), mesh,
    )
    created.objects.append(obj)
    collection.objects.link(obj)
    world = source.matrix_world.copy()
    if origin_strategy == "source_origin":
        world.translation = Vector((0.0, 0.0, 0.0))
        transform = world
    else:
        transform = world
        points = [transform @ vertex.co for vertex in mesh.vertices]
        minimum = Vector(tuple(min(point[axis] for point in points) for axis in range(3)))
        maximum = Vector(tuple(max(point[axis] for point in points) for axis in range(3)))
        if origin_strategy == "world_origin":
            offset = Vector((0.0, 0.0, 0.0))
        elif origin_strategy == "bounds_center":
            offset = (minimum + maximum) * 0.5
        else:
            offset = Vector(((minimum.x + maximum.x) * 0.5, (minimum.y + maximum.y) * 0.5, minimum.z))
        transform = Matrix.Translation(-offset) @ transform
    mesh.transform(transform)
    mesh.update()
    obj.matrix_world = Matrix.Identity(4)
    obj[PREPARED_MARKER] = True
    material_audit = {
        "schema_version": 1,
        "status": "pending",
        "source_object": source.name,
        "prepared_object": obj.name,
        "source_evaluated": {
            "geometry_sha256": snapshot["geometry_sha256"],
            "vertices": snapshot["vertices"],
            "polygons": snapshot["polygons"],
            "triangles": snapshot["triangles"],
            "materials": snapshot["distribution"],
        },
        "old_to_new": [
            {
                "source_slot": old_index,
                "prepared_slot": new_index,
                "material": snapshot["distribution"][old_index]["material"],
                "faces": snapshot["distribution"][old_index]["faces"],
                "triangles": snapshot["distribution"][old_index]["triangles"],
            }
            for old_index, new_index in sorted(old_to_new.items())
        ],
    }
    return obj, material_audit


def _finalize_material_audit(obj, audit: dict[str, Any]) -> dict[str, Any]:
    usage = inspect_mesh_material_usage(obj.data)
    if usage.invalid_indices:
        raise ToolchainError(
            "PREPARE", "static.prepared_material_index",
            "Prepared polygons reference material slots that do not exist",
            {"object": obj.name, "invalid_indices": list(usage.invalid_indices)},
        )
    expected: dict[int, dict[str, Any]] = {}
    for item in audit["old_to_new"]:
        prepared_slot = int(item["prepared_slot"])
        record = expected.setdefault(prepared_slot, {
            "source_slots": [], "source_materials": [], "faces": 0, "triangles": 0,
        })
        record["source_slots"].append(int(item["source_slot"]))
        record["source_materials"].append(item["material"])
        record["faces"] += int(item["faces"])
        record["triangles"] += int(item["triangles"])

    prepared_materials = []
    mismatches = []
    for item in usage.distribution:
        slot = int(item["slot"])
        source = expected.get(slot)
        if source is None:
            mismatches.append({"slot": slot, "reason": "unexpected_prepared_slot"})
            continue
        token = item["token"]
        if not token:
            mismatches.append({"slot": slot, "reason": "missing_logical_token"})
        if (int(item["faces"]), int(item["triangles"])) != (
            source["faces"], source["triangles"],
        ):
            mismatches.append({
                "slot": slot,
                "reason": "surface_count_changed",
                "expected": {"faces": source["faces"], "triangles": source["triangles"]},
                "actual": {"faces": item["faces"], "triangles": item["triangles"]},
            })
        prepared_materials.append({
            "slot": slot,
            "material": item["material"],
            "token": token,
            "faces": int(item["faces"]),
            "triangles": int(item["triangles"]),
            "used": bool(item["used"]),
            "source_slots": sorted(source["source_slots"]),
            "source_materials": source["source_materials"],
        })
    missing_slots = sorted(set(expected) - {item["slot"] for item in usage.distribution})
    mismatches.extend({"slot": slot, "reason": "missing_prepared_slot"} for slot in missing_slots)
    if mismatches:
        raise ToolchainError(
            "PREPARE", "static.evaluated_material_mapping",
            "Prepared materials no longer match the evaluated source surface",
            {"object": obj.name, "mismatches": mismatches},
        )

    audit = dict(audit)
    audit["status"] = "pass"
    audit["prepared"] = {
        "vertices": len(obj.data.vertices),
        "polygons": len(obj.data.polygons),
        "triangles": usage.triangles,
        "materials": prepared_materials,
    }
    audit["logical_token_triangles"] = aggregate_token_triangles(prepared_materials)
    obj[STATIC_MATERIAL_AUDIT_PROPERTY] = json.dumps(
        audit, sort_keys=True, ensure_ascii=False,
    )
    return audit


def _activate_uv(obj, uv_layer: str) -> None:
    layer = obj.data.uv_layers.get(uv_layer)
    if layer is None:
        raise ToolchainError(
            "PREPARE", "static.uv_layer", "The requested evaluated UV layer is missing",
            {"object": obj.name, "uv_layer": uv_layer},
        )
    obj.data.uv_layers.active = layer
    for item in obj.data.uv_layers:
        item.active_render = item == layer


def _smart_project(obj, uv_layer: str, texture_size: int) -> None:
    layer = obj.data.uv_layers.get(uv_layer) or obj.data.uv_layers.new(name=uv_layer)
    obj.data.uv_layers.active = layer
    for item in obj.data.uv_layers:
        item.active_render = item == layer
    view_layer = bpy.context.view_layer
    for item in view_layer.objects:
        item.select_set(False)
    obj.select_set(True)
    view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        bpy.ops.mesh.select_all(action="SELECT")
        result = bpy.ops.uv.smart_project(
            angle_limit=math.radians(66.0),
            margin_method="SCALED",
            rotate_method="AXIS_ALIGNED_Y",
            island_margin=8.0 / float(texture_size),
            area_weight=0.0,
            correct_aspect=True,
            scale_to_bounds=True,
        )
        if result != {"FINISHED"}:
            raise ToolchainError(
                "PREPARE", "static.smart_project", "Blender Smart UV Project did not finish",
                {"result": sorted(result)},
            )
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
    _activate_uv(obj, uv_layer)


def _replace_closures_with_emission(material) -> None:
    _surface, unsupported = _surface_nodes(material)
    if unsupported:
        raise ToolchainError(
            "PREPARE", "static.unlit_material_unsupported",
            "Unlit bake found a material closure that cannot be converted mechanically",
            {"material": material.name, "unsupported": unsupported},
        )
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    for node in list(nodes):
        if node.type not in {"BSDF_PRINCIPLED", "BSDF_DIFFUSE", "EMISSION"}:
            continue
        color = node.inputs.get("Base Color") or node.inputs.get("Color")
        if node.type == "EMISSION":
            strength = node.inputs.get("Strength")
            if strength is not None:
                for link in list(strength.links):
                    links.remove(link)
                strength.default_value = 1.0
            continue
        emission = nodes.new("ShaderNodeEmission")
        emission.name = node.name + "_Unlit"
        emission.label = "GoldSrc unlit color"
        emission.location = node.location
        if color is not None:
            if color.is_linked:
                for link in list(color.links):
                    links.new(link.from_socket, emission.inputs["Color"])
            else:
                emission.inputs["Color"].default_value = color.default_value
        emission.inputs["Strength"].default_value = 1.0
        outgoing = [link for link in links if link.from_node == node]
        for link in outgoing:
            destination = link.to_socket
            links.remove(link)
            links.new(emission.outputs["Emission"], destination)
        nodes.remove(node)


def _set_scalar_input(socket, value, links) -> None:
    if isinstance(value, (int, float)):
        socket.default_value = float(value)
    else:
        links.new(value, socket)


def _scalar_math(nodes, links, operation: str, left, right):
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        if operation == "MULTIPLY":
            return float(left) * float(right)
        if operation == "ADD":
            return float(left) + float(right)
        if operation == "SUBTRACT":
            return float(left) - float(right)
    node = nodes.new("ShaderNodeMath")
    node.operation = operation
    _set_scalar_input(node.inputs[0], left, links)
    _set_scalar_input(node.inputs[1], right, links)
    return node.outputs[0]


def _mix_scalar(nodes, links, first, second, factor):
    if isinstance(factor, (int, float)):
        amount = max(0.0, min(1.0, float(factor)))
        if amount <= 0.0:
            return first
        if amount >= 1.0:
            return second
        inverse = 1.0 - amount
    else:
        inverse = _scalar_math(nodes, links, "SUBTRACT", 1.0, factor)
        amount = factor
    left = _scalar_math(nodes, links, "MULTIPLY", first, inverse)
    right = _scalar_math(nodes, links, "MULTIPLY", second, amount)
    return _scalar_math(nodes, links, "ADD", left, right)


def _socket_scalar(socket):
    if socket is None:
        return 1.0
    if socket.is_linked:
        return socket.links[0].from_socket
    return float(socket.default_value)


def _closure_opacity(material, node, active: set[int]):
    pointer = node.as_pointer()
    if pointer in active:
        raise ToolchainError(
            "PREPARE", "static.alpha_graph_cycle",
            "Transparent material contains a cyclic shader graph",
            {"material": material.name, "node": node.name},
        )
    active.add(pointer)
    try:
        if node.type == "BSDF_PRINCIPLED":
            return _socket_scalar(node.inputs.get("Alpha"))
        if node.type in {"BSDF_DIFFUSE", "EMISSION"}:
            return 1.0
        if node.type == "BSDF_TRANSPARENT":
            return 0.0
        if node.type == "MIX_SHADER":
            shader_inputs = [socket for socket in node.inputs if socket.type == "SHADER"]
            if len(shader_inputs) != 2 or any(not socket.is_linked for socket in shader_inputs):
                raise ToolchainError(
                    "PREPARE", "static.alpha_material_unsupported",
                    "Transparent Mix Shader inputs must both be linked",
                    {"material": material.name, "node": node.name},
                )
            factor = _socket_scalar(node.inputs[0])
            first = _closure_opacity(material, shader_inputs[0].links[0].from_node, active)
            second = _closure_opacity(material, shader_inputs[1].links[0].from_node, active)
            return _mix_scalar(material.node_tree.nodes, material.node_tree.links, first, second, factor)
        raise ToolchainError(
            "PREPARE", "static.alpha_material_unsupported",
            "Transparent shader closure cannot be converted to an opacity bake",
            {"material": material.name, "node": node.name, "type": node.bl_idname},
        )
    finally:
        active.remove(pointer)


def _configure_alpha_bake_material(material, target_image) -> None:
    unsupported = _alpha_bake_unsupported(material)
    if unsupported:
        raise ToolchainError(
            "PREPARE", "static.alpha_material_unsupported",
            "Transparent material alpha cannot be baked mechanically",
            {"material": material.name, "unsupported": unsupported},
        )
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    outputs = [
        node for node in nodes
        if node.type == "OUTPUT_MATERIAL" and node.is_active_output
    ]
    if not outputs:
        output = nodes.new("ShaderNodeOutputMaterial")
        opacity = float(material.diffuse_color[3])
        outputs = [output]
    else:
        surface = outputs[0].inputs.get("Surface")
        if surface is None or not surface.is_linked:
            opacity = float(material.diffuse_color[3])
        else:
            opacity = _closure_opacity(material, surface.links[0].from_node, set())
            if isinstance(opacity, (int, float)) and float(opacity) >= 0.999999:
                opacity = min(float(opacity), float(material.diffuse_color[3]))
    emission = nodes.new("ShaderNodeEmission")
    emission.name = "GoldSrcAlphaEmission"
    emission.inputs["Strength"].default_value = 1.0
    if isinstance(opacity, (int, float)):
        value = max(0.0, min(1.0, float(opacity)))
        emission.inputs["Color"].default_value = (value, value, value, 1.0)
    else:
        links.new(opacity, emission.inputs["Color"])
    for output in outputs:
        for socket_name in ("Surface", "Volume", "Displacement"):
            socket = output.inputs.get(socket_name)
            if socket is not None:
                for link in list(socket.links):
                    links.remove(link)
        links.new(emission.outputs["Emission"], output.inputs["Surface"])
    target = nodes.new("ShaderNodeTexImage")
    target.name = "GoldSrcAlphaBakeTarget"
    target.image = target_image
    for node in nodes:
        node.select = False
    target.select = True
    nodes.active = target


def _merge_baked_alpha(color_image, alpha_image) -> None:
    try:
        import numpy
    except ImportError as exc:
        raise ToolchainError(
            "PREPARE", "static.alpha_numpy",
            "Blender's NumPy runtime is required to merge baked alpha",
            {},
        ) from exc
    if tuple(int(value) for value in color_image.size) != tuple(int(value) for value in alpha_image.size):
        raise ToolchainError(
            "PREPARE", "static.alpha_size",
            "Color and alpha bake targets have different dimensions",
            {
                "color": [int(value) for value in color_image.size],
                "alpha": [int(value) for value in alpha_image.size],
            },
        )
    channel_count = len(color_image.pixels)
    color = numpy.empty(channel_count, dtype=numpy.float32)
    alpha = numpy.empty(channel_count, dtype=numpy.float32)
    color_image.pixels.foreach_get(color)
    alpha_image.pixels.foreach_get(alpha)
    color[3::4] = numpy.clip(alpha[0::4], 0.0, 1.0)
    color_image.pixels.foreach_set(color)
    color_image.update()


def _bake_material_alpha(obj, records, uv_layer: str) -> None:
    original_slots = list(obj.data.materials)
    alpha_materials = []
    alpha_images = []
    alpha_by_material = {}
    try:
        for index, record in enumerate(records):
            source = record["source_material"]
            alpha_image = bpy.data.images.new(
                _unique_id_name(bpy.data.images, f"{record['image'].name}_AlphaBake"),
                width=int(record["image"].size[0]),
                height=int(record["image"].size[1]),
                alpha=True,
                float_buffer=False,
            )
            alpha_image.generated_color = (0.0, 0.0, 0.0, 1.0)
            alpha_images.append(alpha_image)
            alpha_material = source.copy()
            alpha_material.name = _unique_id_name(
                bpy.data.materials, f"{record['material'].name}_AlphaBake_{index:02d}",
            )
            alpha_materials.append(alpha_material)
            _configure_alpha_bake_material(alpha_material, alpha_image)
            alpha_by_material[record["material"].as_pointer()] = alpha_material
            record["alpha_image"] = alpha_image
        obj.data.materials.clear()
        for material in original_slots:
            obj.data.materials.append(alpha_by_material[material.as_pointer()])
        result = bpy.ops.object.bake(
            type="EMIT", margin=16, margin_type="EXTEND", use_clear=True,
            uv_layer=uv_layer,
        )
        if result != {"FINISHED"}:
            raise ToolchainError(
                "PREPARE", "static.alpha_bake", "Cycles alpha bake did not finish",
                {"result": sorted(result)},
            )
        for record in records:
            _merge_baked_alpha(record["image"], record["alpha_image"])
    except ToolchainError:
        raise
    except Exception as exc:
        raise ToolchainError(
            "PREPARE", "static.alpha_bake", str(exc), {"type": type(exc).__name__},
        ) from exc
    finally:
        obj.data.materials.clear()
        for material in original_slots:
            obj.data.materials.append(material)
        for record in records:
            record.pop("alpha_image", None)
        for material in reversed(alpha_materials):
            if material.name in bpy.data.materials:
                bpy.data.materials.remove(material)
        for image in reversed(alpha_images):
            if image.name in bpy.data.images:
                bpy.data.images.remove(image)


def _logical_image(name: str, size: int, created: _CreatedData):
    image = bpy.data.images.new(name, width=size, height=size, alpha=True, float_buffer=False)
    image.generated_color = (0.0, 0.0, 0.0, 0.0)
    created.images.append(image)
    return image


def _save_png(image, path: Path, created: _CreatedData) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.filepath_raw = str(path)
    image.file_format = "PNG"
    image.save()
    if not path.is_file() or not path.stat().st_size:
        raise ToolchainError(
            "PREPARE", "static.logical_png", "Blender did not write the logical PNG",
            {"path": str(path), "image": image.name},
        )
    created.files.append(path)


def _set_final_image_material(material, image, token: str, modes: list[str]) -> None:
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    texture = nodes.new("ShaderNodeTexImage")
    texture.image = image
    texture.interpolation = "Closest"
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    output = nodes.new("ShaderNodeOutputMaterial")
    material.node_tree.links.new(texture.outputs["Color"], shader.inputs["Base Color"])
    if set(modes) & ALPHA_MODES:
        material.node_tree.links.new(texture.outputs["Alpha"], shader.inputs["Alpha"])
    material.node_tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    material["goldsrc_texture_token"] = token
    image["goldsrc_texture_token"] = token


def _passthrough_image(material):
    images = []
    if material.use_nodes and material.node_tree:
        images = [
            node.image for node in material.node_tree.nodes
            if node.type == "TEX_IMAGE" and node.image is not None
        ]
    unique = {image.as_pointer(): image for image in images}
    if len(unique) != 1:
        raise ToolchainError(
            "PREPARE", "static.image_passthrough_ambiguous",
            "image_passthrough requires exactly one source image per material",
            {"material": material.name, "images": [image.name for image in unique.values()]},
        )
    return next(iter(unique.values()))


def _prepare_materials(
    obj,
    *,
    root: Path,
    stem: str,
    texture_size: int,
    bake_mode: str,
    uv_layer: str,
    goldsrc_modes,
    created: _CreatedData,
) -> list[dict[str, Any]]:
    source_slots = list(obj.data.materials)
    if not source_slots or any(material is None for material in source_slots):
        raise ToolchainError(
            "PREPARE", "static.material_missing",
            "Every evaluated material slot must contain an explicit material",
            {"object": obj.name, "slots": len(source_slots)},
        )
    unique_sources = []
    source_index = {}
    for material in source_slots:
        pointer = material.as_pointer()
        if pointer not in source_index:
            source_index[pointer] = len(unique_sources)
            unique_sources.append(material)
    records = []
    copies = {}
    for index, source in enumerate(unique_sources):
        token = f"{stem}_{index:02d}.bmp"
        modes = _modes_for(source, token, goldsrc_modes)
        if _material_transparency(source) and not (set(modes) & ALPHA_MODES):
            raise ToolchainError(
                "PREPARE", "static.alpha_mode_required",
                "Transparent material semantics require an explicit GoldSrc alpha mode",
                {"material": source.name, "supported_modes": sorted(ALPHA_MODES)},
            )
        if bake_mode == "unlit_color":
            _surface, unsupported = _surface_nodes(source)
            if unsupported:
                raise ToolchainError(
                    "PREPARE", "static.unlit_material_unsupported",
                    "Unlit bake cannot convert this material without an artistic decision",
                    {"material": source.name, "unsupported": unsupported},
                )
        material = source.copy()
        material.name = _unique_id_name(bpy.data.materials, f"{stem}_{index:02d}_GoldSrc")
        created.materials.append(material)
        if bake_mode == "color_only" and not material.use_nodes:
            color = tuple(material.diffuse_color)
            material.use_nodes = True
            shader = next(
                (node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"),
                None,
            )
            if shader is not None:
                shader.inputs["Base Color"].default_value = color
        copies[source.as_pointer()] = material
        logical_name = f"{stem}_{index:02d}.png"
        logical_path = root / "textures" / "logical" / logical_name
        if logical_path.exists():
            logical_path = logical_path.with_name(f"{logical_path.stem}_{uuid4().hex[:8]}.png")
        if bake_mode == "image_passthrough":
            source_image = _passthrough_image(source)
            if tuple(int(value) for value in source_image.size) != (texture_size, texture_size):
                raise ToolchainError(
                    "PREPARE", "static.passthrough_size",
                    "image_passthrough source dimensions must equal texture_size",
                    {
                        "material": source.name,
                        "image": source_image.name,
                        "source_size": [int(value) for value in source_image.size],
                        "texture_size": texture_size,
                    },
                )
            image = source_image.copy()
            image.name = _unique_id_name(bpy.data.images, f"{stem}_{index:02d}_Logical")
            created.images.append(image)
            _save_png(image, logical_path, created)
        else:
            image = _logical_image(
                _unique_id_name(bpy.data.images, f"{stem}_{index:02d}_Logical"),
                texture_size,
                created,
            )
            if bake_mode == "unlit_color":
                _replace_closures_with_emission(material)
            target = material.node_tree.nodes.new("ShaderNodeTexImage")
            target.image = image
            target.name = "GoldSrcBakeTarget"
            for node in material.node_tree.nodes:
                node.select = False
            target.select = True
            material.node_tree.nodes.active = target
        records.append({
            "source_material": source,
            "material": material,
            "image": image,
            "token": token,
            "modes": modes,
            "logical_path": logical_path,
        })
    obj.data.materials.clear()
    for source in source_slots:
        obj.data.materials.append(copies[source.as_pointer()])
    if bake_mode != "image_passthrough":
        scene = bpy.context.scene
        previous_engine = scene.render.engine
        view_layer = bpy.context.view_layer
        try:
            scene.render.engine = "CYCLES"
            for item in view_layer.objects:
                item.select_set(False)
            obj.select_set(True)
            view_layer.objects.active = obj
            arguments = {
                "type": "EMIT" if bake_mode == "unlit_color" else "DIFFUSE",
                "margin": 16,
                "margin_type": "EXTEND",
                "use_clear": True,
                "uv_layer": uv_layer,
            }
            if bake_mode == "color_only":
                arguments["pass_filter"] = {"COLOR"}
            result = bpy.ops.object.bake(**arguments)
            if result != {"FINISHED"}:
                raise ToolchainError(
                    "PREPARE", "static.bake", "Cycles color bake did not finish",
                    {"result": sorted(result), "mode": bake_mode},
                )
            if any(_material_transparency(record["source_material"]) for record in records):
                _bake_material_alpha(obj, records, uv_layer)
            for record in records:
                _save_png(record["image"], record["logical_path"], created)
        except ToolchainError:
            raise
        except Exception as exc:
            raise ToolchainError(
                "PREPARE", "static.bake", str(exc),
                {"type": type(exc).__name__, "mode": bake_mode},
            ) from exc
        finally:
            scene.render.engine = previous_engine
    for record in records:
        _set_final_image_material(
            record["material"], record["image"], record["token"], record["modes"],
        )
    return records


def _create_static_rig(obj, collection, created: _CreatedData, stem: str):
    armature_data = bpy.data.armatures.new(
        _unique_id_name(bpy.data.armatures, f"{stem}_GoldSrc_ARM_DATA")
    )
    created.armatures.append(armature_data)
    armature = bpy.data.objects.new(
        _unique_id_name(bpy.data.objects, f"{stem}_GoldSrc_ARM"), armature_data,
    )
    created.objects.append(armature)
    collection.objects.link(armature)
    view_layer = bpy.context.view_layer
    for item in view_layer.objects:
        item.select_set(False)
    armature.select_set(True)
    view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        bone = armature_data.edit_bones.new("root")
        span = max((obj.dimensions), default=1.0)
        bone.head = (0.0, 0.0, 0.0)
        bone.tail = (0.0, 0.0, max(float(span) * 0.1, 1.0))
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
    group = obj.vertex_groups.get("root") or obj.vertex_groups.new(name="root")
    group.add(list(range(len(obj.data.vertices))), 1.0, "REPLACE")
    modifier = obj.modifiers.new("GoldSrc Root", "ARMATURE")
    modifier.object = armature
    obj.parent = armature
    action = bpy.data.actions.new(_unique_id_name(bpy.data.actions, f"{stem}_idle"))
    created.actions.append(action)
    action.use_fake_user = True
    layer = action.layers.new("Static")
    strip = layer.strips.new()
    slot = action.slots.new("OBJECT", armature.name)
    bag = strip.channelbags.new(slot)
    curve = bag.fcurves.new('pose.bones["root"].location', index=0)
    curve.keyframe_points.insert(0.0, 0.0)
    armature.animation_data_create()
    armature.animation_data.action = action
    armature.animation_data.action_slot = slot
    return armature, action


def _evaluated_bounds(obj) -> dict[str, list[float]]:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    try:
        points = [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
        if not points:
            raise ToolchainError(
                "CONTRACT", "static.empty_mesh", "Static contract object has no vertices",
                {"object": obj.name},
            )
        return {
            "min": [min(point[axis] for point in points) for axis in range(3)],
            "max": [max(point[axis] for point in points) for axis in range(3)],
        }
    finally:
        evaluated.to_mesh_clear()


def create_static_contract_from_scene(
    artifacts_dir: str | Path,
    model_name: str,
    request: str,
    *,
    object_name: str,
    armature_name: str,
    action_name: str,
    uv_layer: str,
    textures: list[dict[str, Any]] | None = None,
    large_textures: list[dict[str, Any]] | None = None,
    target_profile: str = "half-life-cs",
    contract_path: str | Path | None = None,
    fps: float = 30.0,
    material_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = resolve_artifact_root(artifacts_dir)
    root.mkdir(parents=True, exist_ok=True)
    obj = bpy.data.objects.get(str(object_name))
    armature = bpy.data.objects.get(str(armature_name))
    action = bpy.data.actions.get(str(action_name))
    if obj is None or obj.type != "MESH":
        raise ToolchainError("CONTRACT", "static.object_missing", "Static mesh object is missing", {"object": object_name})
    if armature is None or armature.type != "ARMATURE":
        raise ToolchainError("CONTRACT", "static.armature_missing", "Static Armature is missing", {"armature": armature_name})
    if action is None:
        raise ToolchainError("CONTRACT", "static.action_missing", "Static idle Action is missing", {"action": action_name})
    bones = list(armature.data.bones)
    if len(bones) != 1 or bones[0].parent is not None:
        raise ToolchainError(
            "CONTRACT", "static.armature_shape", "Static workflow requires one root bone",
            {"armature": armature.name, "bones": [bone.name for bone in bones]},
        )
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    try:
        layer = mesh.uv_layers.get(str(uv_layer))
        if layer is None:
            raise ToolchainError(
                "CONTRACT", "static.uv_layer", "Static contract UV layer is absent from evaluated geometry",
                {"object": obj.name, "uv_layer": uv_layer},
            )
    finally:
        evaluated.to_mesh_clear()
    stem = _short_name(Path(str(model_name)).stem, "model")
    bounds = _evaluated_bounds(obj)
    request = str(request).strip()
    if not request:
        raise ToolchainError("CONTRACT", "static.request", "request must preserve the user's wording", {})
    contract = {
        "version": 2,
        "intent": {
            "request": request,
            "requirements": [{
                "id": "static-export",
                "source": request,
                "evidence_phases": [
                    "preflight", "export", "compile_sven", "mdl_inspect", "sourceio_roundtrip",
                ],
            }],
            "assumptions": [],
        },
        "target_profile": str(target_profile),
        "model_name": Path(str(model_name)).name,
        "scale": 1.0,
        "bones": [{"name": bones[0].name, "parent": None}],
        "bodies": [{"name": "body", "source": f"{stem}_reference.smd", "object": obj.name}],
        "bodygroups": [],
        "textures": list(textures or []),
        "large_textures": list(large_textures or []),
        "skin_families": [],
        "sequences": [{
            "name": "idle", "source": f"{stem}_idle.smd", "action": action.name,
            "fps": float(fps), "frame": [0, 0], "loop": True, "events": [], "motion": [],
        }],
        "hitboxes": [{"group": 0, "bone": bones[0].name, **bounds}],
        "attachments": [],
        "controllers": [],
        "bounds": {"bbox": bounds, "cbox": bounds},
        "texture_bake": {"uv_layer": str(uv_layer), "require_active_render": True},
        "acceptance": {
            "required_phases": [
                "preflight", "export", "compile_sven", "mdl_inspect",
                "sourceio_roundtrip", "visual_review",
            ],
            "visual_views": ["canonical_orthographic"],
            "allow_known_blockers": [],
        },
    }
    if material_audit is not None:
        contract[STATIC_MATERIAL_AUDIT_FIELD] = material_audit
    try:
        normalized = validate_contract(contract, artifact_dir=root, require_files=False)
    except Exception as exc:
        raise ToolchainError(
            "CONTRACT", "static.contract_invalid", str(exc),
            {"type": type(exc).__name__},
        ) from exc
    if contract_path is None:
        path = root / "contracts" / f"{stem}_{uuid4().hex[:8]}.json"
    else:
        candidate = Path(contract_path).expanduser()
        path = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ToolchainError(
                "CONTRACT", "static.contract_escape", "Contract path must stay inside artifacts_dir",
                {"path": str(path), "artifacts_dir": str(root)},
            ) from exc
    write_json(path, normalized)
    return {
        "status": "pass",
        "contract_path": str(path),
        "artifacts_dir": str(root),
        "object": obj.name,
        "armature": armature.name,
        "action": action.name,
        "uv_layer": str(uv_layer),
        "facts": {
            "bounds": bounds,
            "textures": len(normalized["textures"]),
            "logical_large_textures": len(normalized["large_textures"]),
        },
    }


def prepare_static_export(
    analysis: dict[str, Any],
    *,
    artifacts_dir: str | Path,
    model_name: str,
    request: str,
    texture_size: int | None = None,
    uv_strategy: str | None = None,
    uv_layer: str | None = None,
    origin_strategy: str | None = None,
    bake_mode: str | None = None,
    goldsrc_modes=None,
    target_profile: str = "half-life-cs",
) -> dict[str, Any]:
    analysis_id = analysis["analysis_id"]
    decisions = []
    if texture_size is None:
        decisions.append(_decision("texture_size", [], "explicit_texture_resolution_required"))
    if uv_strategy is None:
        decisions.append(_decision("uv_strategy", sorted(UV_STRATEGIES), "explicit_uv_strategy_required"))
    elif uv_strategy not in UV_STRATEGIES:
        raise ToolchainError("PREPARE", "static.uv_strategy", "Unsupported uv_strategy", {"uv_strategy": uv_strategy})
    if uv_strategy == "existing" and not uv_layer:
        decisions.append(_decision("uv_layer", [], "existing_uv_requires_exact_layer"))
    if origin_strategy is None:
        decisions.append(_decision("origin_strategy", sorted(ORIGIN_STRATEGIES), "explicit_origin_strategy_required"))
    elif origin_strategy not in ORIGIN_STRATEGIES:
        raise ToolchainError("PREPARE", "static.origin_strategy", "Unsupported origin_strategy", {"origin_strategy": origin_strategy})
    if bake_mode is None:
        decisions.append(_decision("bake_mode", sorted(BAKE_MODES), "explicit_bake_mode_required"))
    elif bake_mode not in BAKE_MODES:
        raise ToolchainError("PREPARE", "static.bake_mode", "Unsupported bake_mode", {"bake_mode": bake_mode})
    analyzed_used_materials = [
        item for item in analysis["facts"]["materials"] if item.get("used")
    ]
    transparent = [item["name"] for item in analyzed_used_materials if item["transparent"]]
    if transparent and goldsrc_modes is None:
        decisions.append(_decision(
            "goldsrc_modes", sorted(ALPHA_MODES), "transparent_material_semantics",
            materials=transparent,
        ))
    if decisions:
        return _needs_decision(analysis_id, decisions)
    size = _validate_texture_size(texture_size)
    obj = _resolve_mesh(analysis["object_name"])
    current_facts, current_fingerprint = _analyze_object(obj)
    if current_fingerprint != analysis["fingerprint"]:
        raise ToolchainError(
            "PREPARE", "static.analysis_stale",
            "The source Scene changed after analysis; analyze_selected_static must run again",
            {"analysis_id": analysis_id, "object": obj.name},
        )
    if uv_strategy == "existing":
        candidates = {
            item["name"]: item for item in current_facts["uv"]["layers"]
        }
        if uv_layer not in candidates or not candidates[uv_layer]["valid"]:
            return _needs_decision(analysis_id, [_decision(
                "uv_layer", sorted(name for name, item in candidates.items() if item["valid"]),
                "existing_uv_layer_is_not_valid", requested=uv_layer,
            )])
    else:
        uv_layer = uv_layer or "GoldSrcUV"
    material_decisions = []
    used_materials = [item for item in current_facts["materials"] if item.get("used")]
    if current_facts.get("invalid_material_indices"):
        material_decisions.append(_decision(
            "materials", [], "evaluated_polygons_reference_missing_material_slots",
            invalid_indices=current_facts["invalid_material_indices"],
        ))
    if not used_materials or any(item["name"] is None for item in used_materials):
        material_decisions.append(_decision(
            "materials", [], "every_used_evaluated_material_slot_requires_an_explicit_material",
        ))
    if bake_mode == "unlit_color":
        unsupported = {
            item["name"]: item["unsupported_unlit"]
            for item in used_materials
            if item["unsupported_unlit"]
        }
        if unsupported:
            material_decisions.append(_decision(
                "bake_mode", sorted(BAKE_MODES), "unlit_material_requires_artistic_resolution",
                materials=unsupported,
            ))
    if bake_mode == "image_passthrough":
        ambiguous = {
            item["name"]: [image["name"] for image in item["images"]]
            for item in used_materials
            if len({image["name"] for image in item["images"]}) != 1
        }
        if ambiguous:
            material_decisions.append(_decision(
                "bake_mode", sorted(BAKE_MODES), "image_passthrough_requires_one_source_image_per_material",
                materials=ambiguous,
            ))
    if bake_mode != "image_passthrough":
        unsupported_alpha = {
            item["name"]: item["unsupported_alpha_bake"]
            for item in used_materials
            if item["transparent"] and item["unsupported_alpha_bake"]
        }
        if unsupported_alpha:
            material_decisions.append(_decision(
                "bake_mode", sorted(BAKE_MODES),
                "transparent_alpha_requires_artistic_resolution",
                materials=unsupported_alpha,
            ))
    missing_alpha_modes = []
    for item in used_materials:
        if not item["transparent"]:
            continue
        selected = goldsrc_modes
        if isinstance(goldsrc_modes, dict):
            selected = goldsrc_modes.get(item["name"], goldsrc_modes.get("default", []))
        if not isinstance(selected, (list, tuple, set)) or not (set(selected) & ALPHA_MODES):
            missing_alpha_modes.append(item["name"])
    if missing_alpha_modes:
        material_decisions.append(_decision(
            "goldsrc_modes", sorted(ALPHA_MODES), "transparent_material_semantics",
            materials=missing_alpha_modes,
        ))
    if material_decisions:
        return _needs_decision(analysis_id, material_decisions)
    root = resolve_artifact_root(artifacts_dir)
    root.mkdir(parents=True, exist_ok=True)
    stem = _short_name(Path(str(model_name)).stem, "model", maximum=28)
    state = _capture_context()
    created = _CreatedData()
    try:
        _object_mode()
        collection = bpy.data.collections.new(
            _unique_id_name(bpy.data.collections, f"GoldSrcPrepared_{stem}_{analysis_id[:8]}")
        )
        created.collections.append(collection)
        bpy.context.scene.collection.children.link(collection)
        collection[PREPARED_MARKER] = analysis_id
        prepared, material_audit = _freeze_mesh(
            obj, collection, origin_strategy, created, stem,
        )
        if uv_strategy == "smart_project":
            _smart_project(prepared, str(uv_layer), size)
        else:
            _activate_uv(prepared, str(uv_layer))
        material_records = _prepare_materials(
            prepared,
            root=root,
            stem=stem,
            texture_size=size,
            bake_mode=bake_mode,
            uv_layer=str(uv_layer),
            goldsrc_modes=goldsrc_modes,
            created=created,
        )
        material_audit = _finalize_material_audit(prepared, material_audit)
        armature, action = _create_static_rig(prepared, collection, created, stem)
        scene = bpy.context.scene
        scene.frame_start = 0
        scene.frame_end = 0
        scene.frame_set(0)
        textures = []
        large_textures = []
        for record in material_records:
            relative_png = record["logical_path"].relative_to(root).as_posix()
            if size > 512:
                large_textures.append({
                    "name": record["token"],
                    "image": relative_png,
                    "width": size,
                    "height": size,
                    "tile_size": 512,
                    "modes": record["modes"],
                })
            else:
                textures.append({
                    "name": record["token"],
                    "source": record["token"],
                    "width": size,
                    "height": size,
                    "modes": record["modes"],
                })
        contract_result = create_static_contract_from_scene(
            root,
            Path(str(model_name)).name,
            request,
            object_name=prepared.name,
            armature_name=armature.name,
            action_name=action.name,
            uv_layer=str(uv_layer),
            textures=textures,
            large_textures=large_textures,
            target_profile=target_profile,
            material_audit=material_audit,
        )
        contract_path = Path(contract_result["contract_path"])
        created.files.append(contract_path)
        checkpoint = root / "checkpoints" / f"{stem}_author_{analysis_id[:8]}.blend"
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        before_file = bpy.data.filepath
        result = bpy.ops.wm.save_as_mainfile(
            filepath=str(checkpoint), copy=True, relative_remap=False,
        )
        if result != {"FINISHED"} or not checkpoint.is_file():
            raise ToolchainError(
                "PREPARE", "static.author_checkpoint", "Author checkpoint copy was not written",
                {"path": str(checkpoint), "result": sorted(result)},
            )
        if bpy.data.filepath != before_file:
            raise ToolchainError(
                "PREPARE", "static.author_session_changed",
                "Saving the author checkpoint changed the active Blend file",
                {"before": before_file, "after": bpy.data.filepath},
            )
        created.files.append(checkpoint)
        return {
            "status": "pass",
            "analysis_id": analysis_id,
            "contract_path": str(contract_path),
            "artifacts_dir": str(root),
            "author_checkpoint": str(checkpoint),
            "prepared": {
                "collection": collection.name,
                "object": prepared.name,
                "armature": armature.name,
                "action": action.name,
                "uv_layer": str(uv_layer),
                "logical_pngs": [str(record["logical_path"]) for record in material_records],
                "material_audit": material_audit,
            },
            "facts": {
                "source_object": obj.name,
                "source_fingerprint": current_fingerprint,
                "evaluated_vertices": len(prepared.data.vertices),
                "evaluated_triangles": sum(len(polygon.vertices) - 2 for polygon in prepared.data.polygons),
                "texture_size": size,
                "logical_textures": len(material_records),
                "material_mapping": {
                    "source_materials": len([
                        item for item in material_audit["source_evaluated"]["materials"]
                        if item["used"]
                    ]),
                    "prepared_materials": len(material_audit["prepared"]["materials"]),
                    "logical_token_triangles": material_audit["logical_token_triangles"],
                },
                "uv_strategy": uv_strategy,
                "origin_strategy": origin_strategy,
                "bake_mode": bake_mode,
            },
        }
    except ToolchainError:
        created.cleanup()
        raise
    except Exception as exc:
        created.cleanup()
        raise ToolchainError(
            "PREPARE", "static.prepare", str(exc), {"type": type(exc).__name__},
        ) from exc
    finally:
        _restore_context(state, restore_timeline=False)
