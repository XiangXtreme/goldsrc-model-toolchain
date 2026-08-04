"""Blender 5.2 scene preflight for a GoldSrc model contract."""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Any

from .material_mapping import (
    STATIC_MATERIAL_AUDIT_FIELD,
    STATIC_MATERIAL_AUDIT_PROPERTY,
    distribution_projection,
    inspect_mesh_material_usage,
)
from .material_tokens import resolve_texture_token
from .model_contract import load_contract, validate_contract
from .smd import GOLDSRC_MAX_MODEL_TRIANGLES, GOLDSRC_MAX_MODEL_VERTICES


def _issue(code: str, message: str, *, severity: str = "error", **context: Any) -> dict[str, Any]:
    return {"severity": severity, "code": code, "message": message, "context": context}


def _without_numeric_suffix(name: str) -> str:
    return re.sub(r"\.\d{3}$", "", name)


def _material_image_hints(material: Any) -> list[str]:
    if not material or not material.use_nodes:
        return []
    return sorted({
        value
        for node in material.node_tree.nodes
        if node.type == "TEX_IMAGE" and node.image
        for value in (
            getattr(node.image, "name", ""),
            Path(getattr(node.image, "filepath", "")).name,
        )
        if value
    })


def _material_token_candidates(material: Any) -> list[str]:
    candidates = []
    getter = getattr(material, "get", None)
    custom = getter("goldsrc_texture_token") if callable(getter) else None
    if isinstance(custom, str):
        candidates.append(custom)
    candidates.extend(_material_image_hints(material))
    name = getattr(material, "name", None)
    if isinstance(name, str):
        candidates.extend((name, name + ".bmp"))
    return candidates


def _uv_layer_state(uv_layers: Any) -> dict[str, Any]:
    """Return UV names and active-render state without requiring Blender RNA types."""

    active = getattr(uv_layers, "active", None)
    try:
        layers = list(uv_layers) if uv_layers is not None else []
    except TypeError:
        layers = []
    names = [getattr(layer, "name", None) for layer in layers]
    names = [name for name in names if isinstance(name, str)]
    active_render = next(
        (
            getattr(layer, "name", None)
            for layer in layers
            if bool(getattr(layer, "active_render", False))
            and isinstance(getattr(layer, "name", None), str)
        ),
        None,
    )
    if active_render is None:
        collection_active_render = getattr(uv_layers, "active_render", None)
        if isinstance(collection_active_render, str):
            active_render = collection_active_render
        else:
            candidate = getattr(collection_active_render, "name", None)
            if isinstance(candidate, str):
                active_render = candidate
    return {
        "layers": names,
        "active_uv": getattr(active, "name", None),
        "active_render_uv": active_render,
    }


def _evaluated_mesh_counts(obj: Any, bpy: Any) -> tuple[int, int, int]:
    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
    except (AttributeError, RuntimeError, TypeError):
        triangles = sum(max(0, len(polygon.vertices) - 2) for polygon in obj.data.polygons)
        return len(obj.data.vertices), len(obj.data.polygons), triangles
    try:
        mesh.calc_loop_triangles()
        return len(mesh.vertices), len(mesh.polygons), len(mesh.loop_triangles)
    finally:
        evaluated.to_mesh_clear()


def _evaluated_surface_facts(obj: Any, bpy: Any) -> dict[str, Any]:
    """Inspect the UV/material data that EXPORT will read after modifiers."""

    facts: dict[str, Any] = {
        "available": False,
        "uv_layers": [],
        "active_uv": None,
        "active_render_uv": None,
        "uv_loop_count": 0,
        "uv_bounds": None,
        "uv_nonfinite": 0,
        "material_slots": [],
        "material_distribution": [],
        "invalid_material_indices": [],
        "vertices": 0,
        "polygons": 0,
        "triangles": 0,
    }
    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    except (AttributeError, RuntimeError, TypeError):
        return facts
    try:
        facts["available"] = True
        facts["vertices"] = len(getattr(mesh, "vertices", ()))
        facts["polygons"] = len(getattr(mesh, "polygons", ()))
        uv_layers = getattr(mesh, "uv_layers", None)
        uv_state = _uv_layer_state(uv_layers)
        facts["uv_layers"] = uv_state["layers"]
        facts["active_render_uv"] = uv_state["active_render_uv"]
        active = getattr(uv_layers, "active", None)
        if active is not None:
            facts["active_uv"] = getattr(active, "name", None)
            data = getattr(active, "data", ())
            facts["uv_loop_count"] = len(data)
            coordinates = []
            nonfinite = 0
            for item in data:
                value = getattr(item, "uv", None)
                try:
                    x = float(value.x)
                    y = float(value.y)
                except (AttributeError, TypeError, ValueError):
                    nonfinite += 1
                    continue
                if not (math.isfinite(x) and math.isfinite(y)):
                    nonfinite += 1
                    continue
                coordinates.append((x, y))
            facts["uv_nonfinite"] = nonfinite
            if coordinates:
                facts["uv_bounds"] = {
                    "min": [min(value[0] for value in coordinates), min(value[1] for value in coordinates)],
                    "max": [max(value[0] for value in coordinates), max(value[1] for value in coordinates)],
                }
        materials = getattr(mesh, "materials", ())
        if not len(materials):
            for slot in getattr(obj, "material_slots", ()):
                materials.append(slot.material)
        usage = inspect_mesh_material_usage(mesh)
        facts["material_slots"] = [
            getattr(material, "name", None) for material in materials if material is not None
        ]
        facts["material_distribution"] = list(usage.distribution)
        facts["invalid_material_indices"] = list(usage.invalid_indices)
        facts["triangles"] = usage.triangles
    finally:
        evaluated.to_mesh_clear()
    return facts


def _audit_surface_counts(surface: dict[str, Any]) -> dict[str, int]:
    return {
        "vertices": int(surface.get("vertices", 0)),
        "polygons": int(surface.get("polygons", 0)),
        "triangles": int(surface.get("triangles", 0)),
    }


def _inspect_static_material_audit(
    contract: dict[str, Any],
    bpy: Any,
    mesh_facts: list[dict[str, Any]],
    issues: list[dict[str, Any]],
) -> dict[str, Any] | None:
    audit = contract.get(STATIC_MATERIAL_AUDIT_FIELD)
    if not isinstance(audit, dict):
        return None
    prepared_name = audit["prepared_object"]
    source_name = audit["source_object"]
    prepared = next((item for item in mesh_facts if item["name"] == prepared_name), None)
    source_object = bpy.data.objects.get(source_name)
    failures = []

    if prepared is None:
        failures.append({"surface": "prepared", "reason": "contract_object_missing"})
        prepared_surface = {}
    else:
        prepared_surface = prepared.get("evaluated_surface") or {}
        stored = bpy.data.objects.get(prepared_name).get(STATIC_MATERIAL_AUDIT_PROPERTY)
        try:
            stored_audit = json.loads(stored) if isinstance(stored, str) else None
        except json.JSONDecodeError:
            stored_audit = None
        if stored_audit != audit:
            failures.append({"surface": "prepared", "reason": "object_audit_property_mismatch"})

    if source_object is None or getattr(source_object, "type", None) != "MESH":
        failures.append({"surface": "source_evaluated", "reason": "source_object_missing"})
        source_surface = {}
    else:
        source_surface = _evaluated_surface_facts(source_object, bpy)

    source_expected = distribution_projection(
        audit["source_evaluated"].get("materials", []),
        include_material=True,
        include_token=False,
    )
    source_actual = distribution_projection(
        source_surface.get("material_distribution", []),
        include_material=True,
        include_token=False,
    )
    prepared_expected = distribution_projection(
        audit["prepared"].get("materials", []),
        include_material=False,
        include_token=True,
    )
    prepared_actual = distribution_projection(
        prepared_surface.get("material_distribution", []),
        include_material=False,
        include_token=True,
    )
    if source_expected != source_actual:
        failures.append({"surface": "source_evaluated", "reason": "material_distribution_changed"})
    if prepared_expected != prepared_actual:
        failures.append({"surface": "prepared", "reason": "material_distribution_changed"})
    if _audit_surface_counts(audit["source_evaluated"]) != _audit_surface_counts(source_surface):
        failures.append({"surface": "source_evaluated", "reason": "geometry_counts_changed"})
    if _audit_surface_counts(audit["prepared"]) != _audit_surface_counts(prepared_surface):
        failures.append({"surface": "prepared", "reason": "geometry_counts_changed"})
    if source_surface.get("invalid_material_indices"):
        failures.append({
            "surface": "source_evaluated", "reason": "invalid_material_indices",
            "indices": source_surface["invalid_material_indices"],
        })
    if prepared_surface.get("invalid_material_indices"):
        failures.append({
            "surface": "prepared", "reason": "invalid_material_indices",
            "indices": prepared_surface["invalid_material_indices"],
        })

    status = "pass" if not failures else "fail"
    fact = {
        "status": status,
        "source_object": source_name,
        "prepared_object": prepared_name,
        "source_evaluated_materials": source_actual,
        "prepared_materials": prepared_actual,
        "old_to_new": audit.get("old_to_new", []),
        "failures": failures,
    }
    if failures:
        issues.append(_issue(
            "static.evaluated_material_mapping",
            "Prepared material surfaces do not match the selected evaluated source",
            **fact,
        ))
    return fact


def _bounds_facts(obj: Any) -> dict[str, Any]:
    dimensions = getattr(obj, "dimensions", None)
    corners = getattr(obj, "bound_box", None)
    facts = {
        "dimensions": [float(value) for value in dimensions] if dimensions is not None else None,
        "local_bounds": None,
        "world_bounds": None,
    }
    if not corners:
        return facts
    local = [tuple(float(value) for value in corner) for corner in corners]
    facts["local_bounds"] = {
        "min": [min(point[axis] for point in local) for axis in range(3)],
        "max": [max(point[axis] for point in local) for axis in range(3)],
    }
    matrix = getattr(obj, "matrix_world", None)
    if matrix is None:
        world = local
    else:
        from mathutils import Vector

        world = [tuple(float(value) for value in matrix @ Vector(point)) for point in local]
    facts["world_bounds"] = {
        "min": [min(point[axis] for point in world) for axis in range(3)],
        "max": [max(point[axis] for point in world) for axis in range(3)],
    }
    return facts


def inspect_scene(contract: dict[str, Any], *, bpy_module=None) -> dict[str, Any]:
    contract = validate_contract(contract)
    if bpy_module is None:
        import bpy as bpy_module
    bpy = bpy_module
    issues: list[dict[str, Any]] = []
    version = tuple(getattr(bpy.app, "version", (0, 0, 0)))
    if version[:2] != (5, 2):
        issues.append(_issue("blender.version", f"Blender 5.2 is required, found {version}"))

    declared_objects = {
        item.get("object") for item in contract["bodies"] if isinstance(item.get("object"), str)
    }
    for group in contract["bodygroups"]:
        declared_objects.update(
            choice.get("object") for choice in group["choices"] if isinstance(choice.get("object"), str)
        )
    declared_objects.discard(None)
    if declared_objects:
        meshes = [bpy.data.objects.get(name) for name in sorted(declared_objects)]
        missing = [name for name, obj in zip(sorted(declared_objects), meshes) if obj is None]
        for name in missing:
            candidates = sorted(
                obj.name for obj in bpy.data.objects
                if _without_numeric_suffix(obj.name).casefold() == name.casefold()
            )
            issues.append(_issue(
                "scene.object_missing",
                f"contract object is missing: {name}",
                object=name,
                suffix_candidates=candidates,
                suggested_action=f"rename the intended object to {name}" if candidates else None,
            ))
        meshes = [obj for obj in meshes if obj is not None and obj.type == "MESH"]
    else:
        meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH" and getattr(getattr(obj, "vs", None), "export", True)]
    if not meshes:
        issues.append(_issue("scene.no_meshes", "no exported mesh objects were found"))

    bone_names = {bone["name"] for bone in contract["bones"]}
    texture_bake = contract.get("texture_bake")
    texture_bake_uv = texture_bake.get("uv_layer") if isinstance(texture_bake, dict) else None
    require_active_render = texture_bake.get("require_active_render", True) if isinstance(texture_bake, dict) else False
    mesh_facts = []
    for obj in meshes:
        evaluated_vertices, evaluated_polygons, evaluated_triangles = _evaluated_mesh_counts(obj, bpy)
        evaluated_surface = _evaluated_surface_facts(obj, bpy)
        raw_uv_state = _uv_layer_state(getattr(obj.data, "uv_layers", None))
        if evaluated_vertices > GOLDSRC_MAX_MODEL_VERTICES:
            issues.append(_issue(
                "mesh.vertex_budget",
                f"{obj.name} evaluates to {evaluated_vertices} vertices; EXPORT will split triangles into body parts at the bundled {GOLDSRC_MAX_MODEL_VERTICES}-vertex submodel limit",
                severity="warning",
                object=obj.name,
                evaluated_vertices=evaluated_vertices,
                limit=GOLDSRC_MAX_MODEL_VERTICES,
                target_profile=contract["target_profile"],
                export_split=True,
            ))
        if evaluated_triangles > GOLDSRC_MAX_MODEL_TRIANGLES:
            issues.append(_issue(
                "mesh.triangle_budget",
                f"{obj.name} evaluates to {evaluated_triangles} triangles; EXPORT will split triangles into body parts at the bundled {GOLDSRC_MAX_MODEL_TRIANGLES}-triangle submodel limit",
                severity="warning",
                object=obj.name,
                evaluated_polygons=evaluated_polygons,
                evaluated_triangles=evaluated_triangles,
                limit=GOLDSRC_MAX_MODEL_TRIANGLES,
                target_profile=contract["target_profile"],
                export_split=True,
            ))
        if any(abs(value - 1.0) > 0.00001 for value in obj.scale):
            issues.append(_issue("mesh.scale", f"apply scale on {obj.name}", object=obj.name, scale=list(obj.scale)))
        if any(abs(value) > 0.00001 for value in obj.rotation_euler):
            issues.append(_issue("mesh.rotation", f"apply rotation on {obj.name}", object=obj.name, rotation=list(obj.rotation_euler)))
        non_triangles = sum(len(polygon.vertices) != 3 for polygon in obj.data.polygons)
        if non_triangles:
            issues.append(_issue(
                "mesh.non_triangles",
                f"{obj.name} contains {non_triangles} non-triangle polygons; EXPORT triangulates the evaluated mesh",
                severity="warning",
                object=obj.name,
                count=non_triangles,
                export_triangulates=True,
            ))
        if not obj.data.uv_layers or obj.data.uv_layers.active is None:
            issues.append(_issue("mesh.uv_missing", f"{obj.name} has no active UV layer", object=obj.name))
        if raw_uv_state["active_uv"] and raw_uv_state["active_render_uv"] and raw_uv_state["active_uv"] != raw_uv_state["active_render_uv"]:
            issues.append(_issue(
                "mesh.active_render_uv_mismatch",
                f"{obj.name} active UV differs from its active-render UV; baking may read a different layer",
                severity="warning",
                object=obj.name,
                active_uv=raw_uv_state["active_uv"],
                active_render_uv=raw_uv_state["active_render_uv"],
            ))
        if evaluated_surface["available"]:
            if evaluated_surface["active_uv"] is None:
                issues.append(_issue(
                    "mesh.evaluated_uv_missing",
                    f"{obj.name} has no active UV layer after modifier evaluation",
                    object=obj.name,
                ))
            if evaluated_surface["uv_nonfinite"]:
                issues.append(_issue(
                    "mesh.evaluated_uv_nonfinite",
                    f"{obj.name} has non-finite evaluated UV coordinates",
                    object=obj.name,
                    count=evaluated_surface["uv_nonfinite"],
                ))
            if evaluated_surface["active_uv"] and evaluated_surface["active_render_uv"] and evaluated_surface["active_uv"] != evaluated_surface["active_render_uv"]:
                issues.append(_issue(
                    "mesh.evaluated_active_render_uv_mismatch",
                    f"{obj.name} evaluated active UV differs from its evaluated active-render UV",
                    severity="warning",
                    object=obj.name,
                    active_uv=evaluated_surface["active_uv"],
                    active_render_uv=evaluated_surface["active_render_uv"],
                ))
            raw_name = raw_uv_state["active_uv"]
            if raw_name and evaluated_surface["active_uv"] and raw_name != evaluated_surface["active_uv"]:
                issues.append(_issue(
                    "mesh.evaluated_uv_changed",
                    f"{obj.name} active UV differs after modifier evaluation",
                    severity="warning",
                    object=obj.name,
                    source_uv=raw_name,
                    evaluated_uv=evaluated_surface["active_uv"],
                ))
        if texture_bake_uv:
            if not evaluated_surface["available"]:
                issues.append(_issue(
                    "mesh.texture_bake_surface_unavailable",
                    f"{obj.name} texture bake contract cannot be checked without evaluated mesh data",
                    object=obj.name,
                    target_uv=texture_bake_uv,
                ))
            for source, state in (
                ("raw", raw_uv_state),
                ("evaluated", {
                    "layers": evaluated_surface["uv_layers"],
                    "active_uv": evaluated_surface["active_uv"],
                    "active_render_uv": evaluated_surface["active_render_uv"],
                }),
            ):
                if texture_bake_uv not in state["layers"]:
                    issues.append(_issue(
                        "mesh.texture_bake_uv_missing",
                        f"{obj.name} texture bake UV is missing from the {source} mesh",
                        object=obj.name,
                        source=source,
                        target_uv=texture_bake_uv,
                        available_uvs=state["layers"],
                    ))
                if state["active_uv"] != texture_bake_uv:
                    issues.append(_issue(
                        "mesh.texture_bake_uv_not_active",
                        f"{obj.name} texture bake UV is not the {source} active UV used for export",
                        object=obj.name,
                        source=source,
                        target_uv=texture_bake_uv,
                        active_uv=state["active_uv"],
                    ))
                if require_active_render and state["active_render_uv"] != texture_bake_uv:
                    issues.append(_issue(
                        "mesh.texture_bake_uv_not_active_render",
                        f"{obj.name} texture bake UV is not the {source} active-render UV used by Blender baking",
                        object=obj.name,
                        source=source,
                        target_uv=texture_bake_uv,
                        active_render_uv=state["active_render_uv"],
                    ))
        material_slots = [slot.material for slot in obj.material_slots if slot.material]
        materials = [material.name for material in material_slots]
        if not materials:
            issues.append(_issue("mesh.material_missing", f"{obj.name} has no material", object=obj.name))
        resolved_materials = {
            material.name: resolve_texture_token(_material_token_candidates(material), contract)
            for material in material_slots
        }
        unknown_materials = sorted(
            name for name, token in resolved_materials.items() if token is None
        )
        if unknown_materials:
            material_hints = {
                material_name: _material_image_hints(bpy.data.materials.get(material_name))
                for material_name in unknown_materials
            }
            issues.append(_issue(
                "mesh.material_unknown",
                f"{obj.name} uses materials absent from the contract",
                object=obj.name,
                materials=unknown_materials,
                image_filename_hints=material_hints,
                token_candidates={
                    material.name: _material_token_candidates(material)
                    for material in material_slots
                    if material.name in unknown_materials
                },
                declared_materials=sorted(texture["name"] for texture in contract["textures"]),
            ))
        group_names = {group.index: group.name for group in obj.vertex_groups}
        unweighted = 0
        multiweighted = 0
        missing_bone_groups: set[str] = set()
        for vertex in obj.data.vertices:
            influences = [(group_names[item.group], item.weight) for item in vertex.groups if item.weight > 0.000001]
            if not influences:
                unweighted += 1
            elif len(influences) != 1 or not math.isclose(influences[0][1], 1.0, abs_tol=0.0001):
                multiweighted += 1
            for group_name, _weight in influences:
                if group_name not in bone_names:
                    missing_bone_groups.add(group_name)
        if unweighted:
            issues.append(_issue("weights.unweighted", f"{obj.name} has {unweighted} unweighted vertices", object=obj.name, count=unweighted))
        if multiweighted:
            issues.append(_issue("weights.multiple", f"{obj.name} has {multiweighted} vertices without exactly one 1.0 influence", object=obj.name, count=multiweighted))
        if missing_bone_groups:
            issues.append(_issue("weights.unknown_bone", f"{obj.name} uses vertex groups absent from the contract", object=obj.name, groups=sorted(missing_bone_groups)))
        mesh_facts.append({
            "name": obj.name,
            "vertices": len(obj.data.vertices),
            "polygons": len(obj.data.polygons),
            "evaluated_vertices": evaluated_vertices,
            "evaluated_polygons": evaluated_polygons,
            "evaluated_triangles": evaluated_triangles,
            "materials": materials,
            "material_tokens": resolved_materials,
            "unweighted": unweighted,
            "multiweighted": multiweighted,
            "raw_uv": raw_uv_state,
            "texture_bake": {
                "target_uv": texture_bake_uv,
                "require_active_render": bool(require_active_render),
            } if texture_bake_uv else None,
            "evaluated_surface": evaluated_surface,
            **_bounds_facts(obj),
        })

    static_material_audit = _inspect_static_material_audit(
        contract, bpy, mesh_facts, issues,
    )

    armatures = []
    seen_armatures = set()
    for mesh in meshes:
        candidates = []
        parent = getattr(mesh, "parent", None)
        if parent and parent.type == "ARMATURE":
            candidates.append(parent)
        candidates.extend(
            modifier.object for modifier in getattr(mesh, "modifiers", ())
            if modifier.type == "ARMATURE" and modifier.object is not None
        )
        for armature in candidates:
            if armature.name not in seen_armatures:
                seen_armatures.add(armature.name)
                armatures.append(armature)
    if not armatures:
        scene_armatures = [
            obj for obj in bpy.context.scene.objects
            if getattr(obj, "type", None) == "ARMATURE"
        ]
        if len(scene_armatures) == 1:
            armatures = scene_armatures
    if not armatures:
        issues.append(_issue("armature.missing", "a root bone and armature are required for GoldSrc export"))
    else:
        actual_bones: dict[str, str | None] = {}
        for armature in armatures:
            for bone in armature.data.bones:
                actual_bones[bone.name] = bone.parent.name if bone.parent else None
        expected = {bone["name"]: bone.get("parent") for bone in contract["bones"]}
        if actual_bones != expected:
            issues.append(_issue("armature.contract_mismatch", "Blender bone graph does not match the model contract", expected=expected, actual=actual_bones))

    actions = {action.name: action for action in bpy.data.actions}
    for sequence in contract["sequences"]:
        action_name = sequence.get("action", sequence["name"])
        action = actions.get(action_name)
        if action is None:
            issues.append(_issue("animation.action_missing", f"sequence {sequence['name']} requires Action {action_name}", sequence=sequence["name"], action=action_name))
            continue
        frame_range = sequence.get("frame")
        if frame_range and (action.frame_range[0] > frame_range[0] or action.frame_range[1] < frame_range[1]):
            issues.append(_issue("animation.frame_range", f"Action {action_name} does not cover the declared frame range", action=action_name, action_range=list(action.frame_range), expected=frame_range))

    scene = bpy.context.scene
    declared_action_names = {
        sequence.get("action", sequence["name"])
        for sequence in contract["sequences"]
    }
    active_actions: set[str] = set()
    nla_actions: set[str] = set()
    for armature in armatures:
        animation_data = getattr(armature, "animation_data", None)
        active = getattr(animation_data, "action", None)
        if active is not None:
            active_actions.add(active.name)
        for track in getattr(animation_data, "nla_tracks", ()) if animation_data else ():
            for strip in getattr(track, "strips", ()):
                action = getattr(strip, "action", None)
                if action is not None:
                    nla_actions.add(action.name)
    if contract["sequences"] and not declared_action_names.intersection(active_actions | nla_actions):
        issues.append(_issue(
            "animation.playback_unbound",
            "no contract sequence Action is bound to an armature for viewport playback",
            declared_actions=sorted(declared_action_names),
            active_actions=sorted(active_actions),
            nla_actions=sorted(nla_actions),
        ))
    declared_ranges = [
        sequence["frame"]
        for sequence in contract["sequences"]
        if isinstance(sequence.get("frame"), list) and len(sequence["frame"]) == 2
    ]
    if declared_ranges:
        required_start = min(frame_range[0] for frame_range in declared_ranges)
        required_end = max(frame_range[1] for frame_range in declared_ranges)
        if scene.frame_start > required_start or scene.frame_end < required_end:
            issues.append(_issue(
                "animation.playback_range",
                "scene playback range does not cover the contract sequence range",
                scene_range=[scene.frame_start, scene.frame_end],
                required_range=[required_start, required_end],
            ))
    if scene.frame_current != scene.frame_start:
        issues.append(_issue(
            "animation.playback_start",
            "save the author checkpoint at scene.frame_start so Space starts at the intended frame",
            frame_current=scene.frame_current,
            frame_start=scene.frame_start,
        ))

    status = "pass" if not any(item["severity"] == "error" for item in issues) else "fail"
    return {
        "status": status,
        "blender_version": ".".join(map(str, version)),
        "issues": issues,
        "facts": {
            "meshes": mesh_facts,
            "static_material_audit": static_material_audit,
            "armatures": len(armatures),
            "actions": sorted(actions),
            "playback": {
                "frame_start": scene.frame_start,
                "frame_current": scene.frame_current,
                "frame_end": scene.frame_end,
                "active_actions": sorted(active_actions),
                "nla_actions": sorted(nla_actions),
                "ready": not any(item["code"].startswith("animation.playback_") for item in issues),
            },
        },
    }


def write_preflight(contract_path: Path | str, output_path: Path | str) -> dict[str, Any]:
    contract = load_contract(contract_path)
    report = inspect_scene(contract)
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[sys.argv.index("--") + 1 :] if argv is None and "--" in sys.argv else (argv or sys.argv[1:]))
    if len(values) != 2:
        raise SystemExit("usage: blender_preflight.py <model_contract.json> <preflight.json>")
    report = write_preflight(values[0], values[1])
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
