"""Blender 5.2 scene preflight for a GoldSrc model contract."""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Any

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
        Path(node.image.filepath).name
        for node in material.node_tree.nodes
        if node.type == "TEX_IMAGE" and node.image and node.image.filepath
    })


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
        "active_uv": None,
        "uv_loop_count": 0,
        "uv_bounds": None,
        "uv_nonfinite": 0,
        "material_slots": [],
    }
    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    except (AttributeError, RuntimeError, TypeError):
        return facts
    try:
        facts["available"] = True
        uv_layers = getattr(mesh, "uv_layers", None)
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
        facts["material_slots"] = [
            getattr(material, "name", None) for material in materials if material is not None
        ]
    finally:
        evaluated.to_mesh_clear()
    return facts


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
    declared_materials = {texture["name"] for texture in contract["textures"]}
    mesh_facts = []
    for obj in meshes:
        evaluated_vertices, evaluated_polygons, evaluated_triangles = _evaluated_mesh_counts(obj, bpy)
        evaluated_surface = _evaluated_surface_facts(obj, bpy)
        if evaluated_vertices > GOLDSRC_MAX_MODEL_VERTICES:
            issues.append(_issue(
                "mesh.vertex_budget",
                f"{obj.name} evaluates to {evaluated_vertices} vertices; the bundled GoldSrc compiler allows {GOLDSRC_MAX_MODEL_VERTICES} per submodel",
                severity="error",
                object=obj.name,
                evaluated_vertices=evaluated_vertices,
                limit=GOLDSRC_MAX_MODEL_VERTICES,
                target_profile=contract["target_profile"],
            ))
        if evaluated_triangles > GOLDSRC_MAX_MODEL_TRIANGLES:
            issues.append(_issue(
                "mesh.triangle_budget",
                f"{obj.name} evaluates to {evaluated_triangles} triangles; the bundled GoldSrc compiler allows {GOLDSRC_MAX_MODEL_TRIANGLES} per submodel",
                severity="error",
                object=obj.name,
                evaluated_polygons=evaluated_polygons,
                evaluated_triangles=evaluated_triangles,
                limit=GOLDSRC_MAX_MODEL_TRIANGLES,
                target_profile=contract["target_profile"],
            ))
        if any(abs(value - 1.0) > 0.00001 for value in obj.scale):
            issues.append(_issue("mesh.scale", f"apply scale on {obj.name}", object=obj.name, scale=list(obj.scale)))
        if any(abs(value) > 0.00001 for value in obj.rotation_euler):
            issues.append(_issue("mesh.rotation", f"apply rotation on {obj.name}", object=obj.name, rotation=list(obj.rotation_euler)))
        non_triangles = sum(len(polygon.vertices) != 3 for polygon in obj.data.polygons)
        if non_triangles:
            issues.append(_issue("mesh.non_triangles", f"{obj.name} contains {non_triangles} non-triangle polygons", object=obj.name, count=non_triangles))
        if not obj.data.uv_layers or obj.data.uv_layers.active is None:
            issues.append(_issue("mesh.uv_missing", f"{obj.name} has no active UV layer", object=obj.name))
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
            raw_active = getattr(getattr(obj.data, "uv_layers", None), "active", None)
            raw_name = getattr(raw_active, "name", None)
            if raw_name and evaluated_surface["active_uv"] and raw_name != evaluated_surface["active_uv"]:
                issues.append(_issue(
                    "mesh.evaluated_uv_changed",
                    f"{obj.name} active UV differs after modifier evaluation",
                    severity="warning",
                    object=obj.name,
                    source_uv=raw_name,
                    evaluated_uv=evaluated_surface["active_uv"],
                ))
        materials = [slot.material.name for slot in obj.material_slots if slot.material]
        if not materials:
            issues.append(_issue("mesh.material_missing", f"{obj.name} has no material", object=obj.name))
        unknown_materials = sorted(set(materials) - declared_materials)
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
                declared_materials=sorted(declared_materials),
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
            "unweighted": unweighted,
            "multiweighted": multiweighted,
            "evaluated_surface": evaluated_surface,
            **_bounds_facts(obj),
        })

    armatures = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
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
