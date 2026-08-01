"""Independent GoldSrc MDL v10 readback and five-point visual evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import bpy
from mathutils import Vector

from ..core.action_curves import representative_frame_samples
from ..core.errors import ToolchainError
from ..core.model_contract import load_contract
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
    scene.render.film_transparent = False
    world = bpy.data.worlds.get("GoldSrcRoundtripWorld")
    if world is None:
        world = bpy.data.worlds.new("GoldSrcRoundtripWorld")
    scene.world = world
    world.use_nodes = True
    nodes = world.node_tree.nodes
    nodes.clear()
    background = nodes.new("ShaderNodeBackground")
    background.inputs["Color"].default_value = (0.010, 0.018, 0.030, 1.0)
    background.inputs["Strength"].default_value = 0.4
    output = nodes.new("ShaderNodeOutputWorld")
    world.node_tree.links.new(background.outputs["Background"], output.inputs["Surface"])
    minimum, maximum, center = _bounds(objects)
    extent = max((maximum - minimum).length, 0.25)
    lighting_scale = max(extent, 1.0)
    camera_data = bpy.data.cameras.new("GoldSrcRoundtripCamera")
    camera = bpy.data.objects.new("GoldSrcRoundtripCamera", camera_data)
    scene.collection.objects.link(camera)
    camera.location = center + Vector((extent * 1.25, -extent * 1.65, extent * 0.85))
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera_data.lens = 52
    scene.camera = camera
    for name, offset, energy, size in (
        ("GoldSrcKey", (1.1, -0.8, 1.4), 1000.0, 4.0),
        ("GoldSrcFill", (-1.0, -0.3, 0.7), 550.0, 3.0),
    ):
        light_data = bpy.data.lights.new(name, "AREA")
        light_data.energy = energy * lighting_scale
        light_data.shape = "DISK"
        light_data.size = size * lighting_scale
        light = bpy.data.objects.new(name, light_data)
        scene.collection.objects.link(light)
        light.location = center + Vector(offset) * extent
        light.rotation_euler = (center - light.location).to_track_quat("-Z", "Y").to_euler()
    return {
        "minimum": list(minimum), "maximum": list(maximum), "center": list(center),
        "extent": extent, "lighting_scale": lighting_scale,
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
        luminance = [0.2126 * value[0] + 0.7152 * value[1] + 0.0722 * value[2] for value in rgb]
        visible = sum(1 for value in luminance if value > 0.08)
        return {
            "path": str(path),
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "mean_luminance": round(sum(luminance) / max(1, len(luminance)), 6),
            "foreground_fraction": round(visible / max(1, len(luminance)), 6),
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
    bounds = _configure_render(imported["objects"])
    previews = []
    for action in imported["actions"]:
        _bind_action(imported["armature"], action)
        samples = representative_frame_samples(action.frame_range, maximum=5)
        if len(samples) < min(5, max(1, round(action.frame_range[1] - action.frame_range[0] + 1))):
            raise ToolchainError("ROUNDTRIP", "roundtrip.samples", "Action did not produce bounded representative samples", {"action": action.name, "samples": samples})
        hashes = set()
        for frame in samples:
            bpy.context.scene.frame_set(int(round(frame)))
            path = root / f"roundtrip_{action.name}_{int(round(frame)):04d}.png"
            facts = _render(path)
            facts.update({"action": action.name, "frame": frame})
            hashes.add(facts["sha256"])
            previews.append(facts)
        if action.frame_range[1] > action.frame_range[0] and len(hashes) == 1:
            raise ToolchainError(
                "ROUNDTRIP", "roundtrip.static_previews", "Animated Action produced identical five-point previews",
                {"action": action.name, "samples": samples},
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
        "preview_hashes": [preview["sha256"] for preview in previews],
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
        "facts": evidence,
        "known_blockers": [],
        "requirement_evidence": _requirements(contract, evidence),
    }
