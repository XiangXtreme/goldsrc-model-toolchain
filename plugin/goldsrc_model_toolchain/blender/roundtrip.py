"""Independent GoldSrc MDL v10 readback and five-point visual evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import bpy
from mathutils import Vector

from ..core.action_curves import representative_frame_samples
from ..core.errors import ToolchainError
from ..core.model_contract import load_contract
from ..core.visual_evidence import (
    choose_front_axis,
    create_labeled_contact_sheet,
    representative_sample_labels,
    summarize_preview_visibility,
)
from .action_import import local_pose_globals
from .mdl_import import import_mdl


def _bounds(objects):
    points = [obj.matrix_world @ Vector(corner) for obj in objects for corner in obj.bound_box]
    if not points:
        raise ToolchainError("ROUNDTRIP", "roundtrip.bounds", "Imported MDL has no renderable mesh bounds")
    minimum = Vector(tuple(min(point[axis] for point in points) for axis in range(3)))
    maximum = Vector(tuple(max(point[axis] for point in points) for axis in range(3)))
    return minimum, maximum, (minimum + maximum) * 0.5


def _configure_render(objects):
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 512
    scene.render.resolution_y = 512
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = True
    world = bpy.data.worlds.get("GoldSrcRoundtripWorld")
    if world is None:
        world = bpy.data.worlds.new("GoldSrcRoundtripWorld")
    scene.world = world
    world.use_nodes = True
    nodes = world.node_tree.nodes
    nodes.clear()
    background = nodes.new("ShaderNodeBackground")
    background.inputs["Color"].default_value = (0.035, 0.035, 0.035, 1.0)
    background.inputs["Strength"].default_value = 0.65
    output = nodes.new("ShaderNodeOutputWorld")
    world.node_tree.links.new(background.outputs["Background"], output.inputs["Surface"])
    minimum, maximum, center = _bounds(objects)
    framing = choose_front_axis(minimum, maximum)
    axis = framing["axis"]
    spans = framing["spans"]
    extent = max((maximum - minimum).length, 0.25)
    max_span = max(max(spans), 0.25)
    projected_span = max(max(framing["projected_spans"]), 0.25)
    view_direction = Vector(tuple(1.0 if index == axis else 0.0 for index in range(3)))
    camera_data = bpy.data.cameras.new("GoldSrcRoundtripCamera")
    camera = bpy.data.objects.new("GoldSrcRoundtripCamera", camera_data)
    scene.collection.objects.link(camera)
    camera.location = center + view_direction * max(max_span * 2.0, 1.0)
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = projected_span * 1.25
    camera_data.clip_start = max(max_span * 0.0001, 0.01)
    camera_data.clip_end = max(max_span * 4.0, 1000.0)
    scene.camera = camera
    up = Vector((0.0, 0.0, 1.0)) if axis != 2 else Vector((0.0, 1.0, 0.0))
    side = up.cross(view_direction).normalized()
    for name, direction, energy, angle in (
        ("GoldSrcKey", view_direction + side * 0.65 + up * 0.85, 2.0, 0.45),
        ("GoldSrcFill", view_direction - side * 0.75 + up * 0.25, 0.8, 0.7),
    ):
        light_data = bpy.data.lights.new(name, "SUN")
        light_data.energy = energy
        light_data.angle = angle
        light = bpy.data.objects.new(name, light_data)
        scene.collection.objects.link(light)
        light.location = center + direction.normalized() * max(max_span * 2.0, 1.0)
        light.rotation_euler = (center - light.location).to_track_quat("-Z", "Y").to_euler()
    return {
        "minimum": list(minimum), "maximum": list(maximum), "center": list(center),
        "extent": extent,
        "spans": spans,
        "view_axis": framing["axis_name"],
        "camera_type": camera_data.type,
        "camera_location": list(camera.location),
        "orthographic_scale": camera_data.ortho_scale,
        "camera_clip": [camera_data.clip_start, camera_data.clip_end],
    }


def _render(path: Path) -> dict:
    scene = bpy.context.scene
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    data = path.read_bytes()
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        pixels = list(image.pixels)
        rgb = [pixels[index:index + 3] for index in range(0, len(pixels), 4)]
        alpha = pixels[3::4]
        luminance = [0.2126 * value[0] + 0.7152 * value[1] + 0.0722 * value[2] for value in rgb]
        visible_indices = [index for index, value in enumerate(alpha) if value > 0.001]
        foreground_luminance = [luminance[index] for index in visible_indices]
        return {
            "path": str(path),
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "mean_luminance": round(sum(luminance) / max(1, len(luminance)), 6),
            "foreground_mean_luminance": round(
                sum(foreground_luminance) / max(1, len(foreground_luminance)), 6,
            ),
            "foreground_pixels": len(visible_indices),
            "foreground_fraction": round(len(visible_indices) / max(1, len(alpha)), 6),
        }
    finally:
        bpy.data.images.remove(image)


def _bind_action(armature, action):
    if not armature.animation_data:
        armature.animation_data_create()
    armature.animation_data.action = action
    if action.slots:
        armature.animation_data.action_slot = action.slots[0]


def _requirements(contract: dict, evidence: dict) -> list[dict]:
    return [
        {
            "id": requirement["id"],
            "status": "pass",
            "summary": "Independent MDL v10 readback reconstructed geometry, textures, bones and animation",
            "evidence": evidence,
        }
        for requirement in contract.get("intent", {}).get("requirements", [])
        if "sourceio_roundtrip" in requirement.get("evidence_phases", [])
    ]


def _audit_weighted_vertices(imported, *, tolerance: float = 0.001) -> dict:
    """Compare every evaluated single-weight vertex with decoded MDL animation."""

    mdl = imported["mdl"]
    armature = imported["armature"]
    bones = [
        SimpleNamespace(index=index, name=bone.name, parent=bone.parent)
        for index, bone in enumerate(mdl.bones)
    ]
    models = {
        (bodypart_index, model_index): model
        for bodypart_index, bodypart in enumerate(mdl.bodyparts)
        for model_index, model in enumerate(bodypart.models)
    }
    action_sources = {}
    for sequence, blends in zip(mdl.sequences, mdl.animations):
        for blend_index, frames in enumerate(blends):
            name = sequence.name if len(blends) == 1 else f"{sequence.name}_blend{blend_index}"
            action_sources[name] = {index: poses for index, poses in enumerate(frames)}
    maximum_error = 0.0
    checked_vertices = 0
    checked_samples = 0
    worst = None
    dependency_graph = bpy.context.evaluated_depsgraph_get()
    for action in imported["actions"]:
        frames = action_sources.get(action.name)
        if not frames:
            continue
        _bind_action(armature, action)
        source_frames = sorted(frames)
        requested = representative_frame_samples((source_frames[0], source_frames[-1]), maximum=5)
        samples = sorted({min(source_frames, key=lambda value: abs(value - item)) for item in requested})
        for frame in samples:
            bpy.context.scene.frame_set(int(frame))
            bpy.context.view_layer.update()
            pose_globals = local_pose_globals(bones, frames[frame], 1.0)
            for obj in imported["objects"]:
                model = models.get((
                    int(obj.get("goldsrc_bodypart_index", -1)),
                    int(obj.get("goldsrc_bodygroup_choice", -1)),
                ))
                if model is None or not len(model.vertices):
                    continue
                evaluated = obj.evaluated_get(dependency_graph)
                mesh = evaluated.to_mesh()
                try:
                    source_attribute = mesh.attributes.get("goldsrc_source_vertex")
                    if source_attribute is None:
                        raise ToolchainError(
                            "ROUNDTRIP", "roundtrip.vertex_mapping",
                            "Evaluated readback mesh lost its source-vertex mapping",
                            {"object": obj.name},
                        )
                    referenced = {
                        item.vertex
                        for model_mesh in model.meshes
                        for command, _fan in model_mesh.commands
                        for item in command
                    }
                    mapped = [item.value for item in source_attribute.data]
                    for evaluated_index, vertex_index in enumerate(mapped):
                        if vertex_index not in referenced:
                            continue
                        source = model.vertices[vertex_index]
                        bone_index = model.bone_vertices[vertex_index]
                        expected = armature.matrix_world @ (pose_globals[int(bone_index)] @ Vector(source))
                        actual = evaluated.matrix_world @ mesh.vertices[evaluated_index].co
                        error = (actual - expected).length
                        checked_vertices += 1
                        if error > maximum_error:
                            maximum_error = error
                            worst = {
                                "action": action.name, "frame": frame,
                                "object": obj.name, "vertex": vertex_index,
                            }
                finally:
                    evaluated.to_mesh_clear()
            checked_samples += 1
    report = {
        "status": "pass" if maximum_error <= tolerance else "fail",
        "max_position_error": maximum_error,
        "position_tolerance": tolerance,
        "checked_vertices": checked_vertices,
        "checked_samples": checked_samples,
        "worst": worst,
    }
    if report["status"] != "pass":
        raise ToolchainError(
            "ROUNDTRIP", "roundtrip.weighted_vertex_error",
            "Evaluated weighted vertices diverge from decoded MDL animation", report,
        )
    return report


def run_roundtrip(contract_path: str | Path, artifacts_dir: str | Path) -> dict:
    root = Path(artifacts_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    contract = load_contract(contract_path, artifact_dir=root, require_files=True)
    mdl_path = (root / contract["outputs"]["sven_mdl"]).resolve()
    imported = import_mdl(mdl_path, scale=1.0, reset_scene=True)
    if not imported["objects"]:
        raise ToolchainError("ROUNDTRIP", "roundtrip.empty", "MDL readback produced no non-empty mesh")
    if len(imported["armature"].data.bones) != len(contract["bones"]):
        raise ToolchainError(
            "ROUNDTRIP", "roundtrip.bones", "Readback bone count differs from contract",
            {"expected": len(contract["bones"]), "actual": len(imported["armature"].data.bones)},
        )
    if len(imported["materials"]) != len(contract["textures"]):
        raise ToolchainError(
            "ROUNDTRIP", "roundtrip.textures", "Readback embedded texture count differs from contract",
            {"expected": len(contract["textures"]), "actual": len(imported["materials"])},
        )
    bodygroup_issues = []
    for body in contract["bodies"]:
        choices = imported["bodygroups"].get(body["name"])
        if choices is None or len(choices) != 1 or choices[0] is None:
            bodygroup_issues.append({"name": body["name"], "expected": ["studio"], "actual": choices})
    for group in contract["bodygroups"]:
        choices = imported["bodygroups"].get(group["name"])
        expected = [None if choice.get("blank") else "studio" for choice in group["choices"]]
        actual = None if choices is None else [None if choice is None else "studio" for choice in choices]
        if actual != expected:
            bodygroup_issues.append({"name": group["name"], "expected": expected, "actual": actual})
    if bodygroup_issues:
        raise ToolchainError(
            "ROUNDTRIP", "roundtrip.bodygroups", "Readback bodygroup choices differ from contract",
            {"issues": bodygroup_issues, "bodygroups": imported["bodygroups"]},
        )
    expected_families = contract.get("skin_families", [])
    if expected_families:
        actual = imported["skin_families"]
        texture_indices = {
            texture["name"].casefold(): index
            for index, texture in enumerate(contract["textures"])
        }
        expected = [
            [texture_indices[name.casefold()] for name in family]
            for family in expected_families
        ]
        expected_shape = [len(expected), len(expected[0])]
        actual_shape = [len(actual), len(actual[0]) if actual else 0]
        if actual_shape != expected_shape or actual != expected:
            raise ToolchainError(
                "ROUNDTRIP", "roundtrip.skin_families", "Readback skin-family table differs from contract",
                {
                    "expected_shape": expected_shape, "actual_shape": actual_shape,
                    "expected": expected, "actual": actual,
                },
            )
    declared_external = {
        name.casefold()
        for name in contract.get("limitations", {}).get("external_sequence_groups", [])
    }
    actual_external = {
        item["name"].casefold() for item in imported["external_sequence_groups"]
    }
    undeclared_external = sorted(actual_external - declared_external)
    stale_external = sorted(declared_external - actual_external)
    if undeclared_external or stale_external:
        raise ToolchainError(
            "ROUNDTRIP", "roundtrip.external_sequences",
            "MDL external sequence groups do not match the explicit contract limitation",
            {
                "groups": imported["external_sequence_groups"],
                "declared": sorted(declared_external),
                "undeclared": undeclared_external,
                "declared_but_not_external": stale_external,
            },
        )
    expected_embedded = len(contract["sequences"]) - len(imported["external_sequence_groups"])
    if len(imported["actions"]) < expected_embedded:
        raise ToolchainError(
            "ROUNDTRIP", "roundtrip.actions", "Embedded MDL sequences were not reconstructed as Actions",
            {"expected": expected_embedded, "actual": len(imported["actions"])},
        )
    suffixed = [name for name in [obj.name for obj in imported["objects"]] + [action.name for action in imported["actions"]] if name.endswith(".001")]
    if suffixed:
        raise ToolchainError("ROUNDTRIP", "roundtrip.suffix", "Readback created numeric-suffix collisions", {"names": suffixed})
    weighted_vertex_audit = _audit_weighted_vertices(imported)
    bounds = _configure_render(imported["objects"])
    previews = []
    contact_sheets = []
    for action in imported["actions"]:
        _bind_action(imported["armature"], action)
        samples = representative_frame_samples(action.frame_range, maximum=5)
        if len(samples) < min(5, max(1, round(action.frame_range[1] - action.frame_range[0] + 1))):
            raise ToolchainError("ROUNDTRIP", "roundtrip.samples", "Action did not produce bounded representative samples", {"action": action.name, "samples": samples})
        hashes = set()
        action_previews = []
        sample_labels = representative_sample_labels(len(samples))
        for sample_index, frame in enumerate(samples):
            bpy.context.scene.frame_set(int(round(frame)))
            path = root / f"roundtrip_{action.name}_{int(round(frame)):04d}.png"
            facts = _render(path)
            facts.update({
                "action": action.name,
                "frame": frame,
                "sample_label": sample_labels[sample_index],
            })
            hashes.add(facts["sha256"])
            previews.append(facts)
            action_previews.append(facts)
        if action.frame_range[1] > action.frame_range[0] and len(hashes) == 1:
            raise ToolchainError(
                "ROUNDTRIP", "roundtrip.static_previews", "Animated Action produced identical five-point previews",
                {"action": action.name, "samples": samples},
            )
        sheet_path = root / f"roundtrip_{action.name}_contact_sheet.png"
        sheet = create_labeled_contact_sheet(
            [
                {
                    "path": preview["path"],
                    "label": preview["sample_label"],
                    "detail": f"{action.name} | Frame {int(round(preview['frame'])):04d}",
                }
                for preview in action_previews
            ],
            sheet_path,
            title=f"MDL readback | {action.name}",
            columns=min(3, len(action_previews)),
        )
        sheet["action"] = action.name
        sheet["frames"] = [preview["frame"] for preview in action_previews]
        Path(sheet["layout_path"]).write_text(
            json.dumps(sheet, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
        )
        contact_sheets.append(sheet)
    preview_visibility = summarize_preview_visibility(previews)
    if preview_visibility["status"] == "fail":
        raise ToolchainError(
            "ROUNDTRIP", "roundtrip.blank_previews",
            "All generated MDL readback previews contain zero foreground pixels",
            preview_visibility,
        )
    first_action = imported["actions"][0] if imported["actions"] else None
    if first_action:
        _bind_action(imported["armature"], first_action)
        scene = bpy.context.scene
        scene.frame_start = int(round(first_action.frame_range[0]))
        scene.frame_end = int(round(first_action.frame_range[1]))
        scene.frame_set(scene.frame_start)
    blend_path = root / "mdl_roundtrip.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
    evidence = {
        "meshes": [obj.name for obj in imported["objects"]],
        "bones": len(imported["armature"].data.bones),
        "textures": len(imported["materials"]),
        "actions": [action.name for action in imported["actions"]],
        "bodygroups": imported["bodygroups"],
        "skin_family_count": len(imported["skin_families"]),
        "action_matrix_audits": imported["action_matrix_audits"],
        "weighted_vertex_audit": weighted_vertex_audit,
        "preview_hashes": [preview["sha256"] for preview in previews],
        "contact_sheet_hashes": [sheet["sha256"] for sheet in contact_sheets],
        "preview_visibility": preview_visibility,
        "playback": {
            "action": first_action.name if first_action else None,
            "frame_start": bpy.context.scene.frame_start,
            "frame_current": bpy.context.scene.frame_current,
            "frame_end": bpy.context.scene.frame_end,
        },
    }
    return {
        "status": "pass",
        "phase": "sourceio_roundtrip",
        "parser": "SourceIO 5.5.4 derived GoldSrc-only independent reader",
        "mdl": str(mdl_path),
        "blend": str(blend_path),
        "bounds": bounds,
        "previews": previews,
        "contact_sheets": contact_sheets,
        "facts": evidence,
        "known_blockers": [],
        "requirement_evidence": _requirements(contract, evidence),
    }
