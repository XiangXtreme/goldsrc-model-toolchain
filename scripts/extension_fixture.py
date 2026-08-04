"""Clean-profile end-to-end fixture for the GoldSrc Extension stages."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import bpy


def _reset() -> None:
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)
    for datablocks in (bpy.data.meshes, bpy.data.armatures, bpy.data.materials, bpy.data.images):
        for datablock in list(datablocks):
            if datablock.users == 0:
                datablocks.remove(datablock)


def _action(armature):
    action = bpy.data.actions.new("idle")
    action.use_fake_user = True
    layer = action.layers.new("Layer")
    strip = layer.strips.new()
    slot = action.slots.new("OBJECT", armature.name)
    bag = strip.channelbags.new(slot)
    armature.pose.bones["root"].rotation_mode = "XYZ"
    curve = bag.fcurves.new('pose.bones["root"].rotation_euler', index=1)
    curve.keyframe_points.add(5)
    for point, value in zip(
        curve.keyframe_points,
        (0.0, math.pi / 2.0, math.pi, math.pi * 1.5, math.pi * 2.0),
    ):
        point.co = (point.co.x, value)
    for index, point in enumerate(curve.keyframe_points):
        point.co.x = index
        point.interpolation = "LINEAR"
    curve.update()
    armature.animation_data_create()
    armature.animation_data.action = action
    armature.animation_data.action_slot = slot
    return action


def _scene():
    _reset()
    scene = bpy.context.scene
    scene.frame_start = 0
    scene.frame_end = 4
    scene.frame_set(0)
    armature_data = bpy.data.armatures.new("fixture_ARM_DATA")
    armature = bpy.data.objects.new("fixture_ARM", armature_data)
    scene.collection.objects.link(armature)
    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bone = armature_data.edit_bones.new("root")
    bone.head = (0.0, 0.0, 0.0)
    bone.tail = (0.0, 0.0, 1.0)
    bpy.ops.object.mode_set(mode="OBJECT")
    _action(armature)
    mesh = bpy.data.meshes.new("fixture_MESH")
    mesh.from_pydata(
        [
            (-256.0, -32.0, -64.0), (256.0, -32.0, -64.0), (256.0, -32.0, 64.0), (-256.0, -32.0, 64.0),
            (-256.0, 32.0, -64.0), (256.0, 32.0, -64.0), (256.0, 32.0, 64.0), (-256.0, 32.0, 64.0),
        ],
        [],
        [
            (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
            (0, 1, 5), (0, 5, 4), (3, 7, 6), (3, 6, 2),
            (0, 4, 7), (0, 7, 3), (1, 2, 6), (1, 6, 5),
        ],
    )
    mesh.update()
    uv = mesh.uv_layers.new(name="UVMap")
    for polygon in mesh.polygons:
        for offset, loop_index in enumerate(polygon.loop_indices):
            uv.data[loop_index].uv = ((offset == 1), (offset == 2))
    image = bpy.data.images.new("base.bmp", 64, 64, alpha=True)
    pixels = []
    for y in range(64):
        for x in range(64):
            pixels.extend((
                0.03 + 0.85 * x / 63.0,
                0.05 + 0.75 * y / 63.0,
                0.08 + 0.65 * (x + y) / 126.0,
                1.0,
            ))
    image.pixels.foreach_set(pixels)
    image.update()
    material = bpy.data.materials.new("base.bmp")
    material.use_nodes = True
    image_node = material.node_tree.nodes.new("ShaderNodeTexImage")
    image_node.image = image
    mesh.materials.append(material)
    obj = bpy.data.objects.new("body_mesh", mesh)
    scene.collection.objects.link(obj)
    obj.parent = armature
    modifier = obj.modifiers.new("Skeleton", "ARMATURE")
    modifier.object = armature
    group = obj.vertex_groups.new(name="root")
    group.add(list(range(len(mesh.vertices))), 1.0, "REPLACE")
    return obj, armature


def _contract() -> dict:
    return {
        "version": 2,
        "intent": {
            "request": "Build one textured animated fixture.",
            "requirements": [{
                "id": "animated-fixture",
                "source": "one textured animated fixture",
                "evidence_phases": ["preflight", "export", "compile_sven", "mdl_inspect", "sourceio_roundtrip"],
            }],
            "assumptions": [],
        },
        "target_profile": "half-life-cs",
        "model_name": "extension_fixture.mdl",
        "scale": 1.0,
        "bones": [{"name": "root", "parent": None}],
        "bodies": [{"name": "body", "source": "reference.smd", "object": "body_mesh"}],
        "bodygroups": [],
        "textures": [{"name": "base.bmp", "source": "base.bmp", "width": 64, "height": 64, "modes": []}],
        "skin_families": [],
        "sequences": [{
            "name": "idle", "source": "idle.smd", "action": "idle", "fps": 20,
            "frame": [0, 4], "loop": True, "events": [], "motion": [],
        }],
        "hitboxes": [{"group": 0, "bone": "root", "min": [-256, -32, -64], "max": [256, 32, 64]}],
        "attachments": [],
        "controllers": [],
        "bounds": {
            "bbox": {"min": [-300, -300, -300], "max": [300, 300, 300]},
            "cbox": {"min": [-300, -300, -300], "max": [300, 300, 300]},
        },
        "acceptance": {
            "required_phases": ["preflight", "export", "compile_sven", "mdl_inspect", "sourceio_roundtrip"],
            "visual_views": ["three_quarter"],
            "allow_known_blockers": [],
        },
    }


def main() -> dict:
    artifacts = Path(os.environ["GOLDSRC_EXTENSION_FIXTURE"]).expanduser().resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    _scene()
    contract_path = artifacts / "model_contract.json"
    contract_path.write_text(json.dumps(_contract(), indent=2) + "\n", encoding="utf-8")
    results = {}
    for stage, filename in (
        ("PREFLIGHT", "preflight.json"),
        ("EXPORT", "export.json"),
        ("COMPILE", "compile_sven.json"),
        ("INSPECT", "mdl_inspection.json"),
        ("ROUNDTRIP", "sourceio_roundtrip.json"),
    ):
        result = bpy.ops.goldsrc_toolchain.execute_stage(
            stage=stage,
            contract_path=str(contract_path),
            artifacts_dir=str(artifacts),
            report_path=filename,
        )
        report = json.loads((artifacts / filename).read_text(encoding="utf-8"))
        if result != {"FINISHED"} or report.get("status") != "pass":
            raise RuntimeError(f"{stage} failed: operator={result} report={report}")
        results[stage] = report
    repeat_result = bpy.ops.goldsrc_toolchain.execute_stage(
        stage="ROUNDTRIP",
        contract_path=str(contract_path),
        artifacts_dir=str(artifacts),
        report_path="sourceio_roundtrip_repeat.json",
    )
    repeat = json.loads((artifacts / "sourceio_roundtrip_repeat.json").read_text(encoding="utf-8"))
    if repeat_result != {"FINISHED"} or repeat.get("status") != "pass":
        raise RuntimeError(f"repeated ROUNDTRIP failed: operator={repeat_result} report={repeat}")
    repeated_names = repeat["facts"]["meshes"] + repeat["facts"]["actions"]
    if any(name.endswith(".001") for name in repeated_names):
        raise RuntimeError(f"repeated ROUNDTRIP created numeric suffixes: {repeated_names}")
    foreground = [preview["foreground_fraction"] for preview in results["ROUNDTRIP"]["previews"]]
    if not foreground or max(foreground) <= 0.0:
        raise RuntimeError(f"bright fixture texture is not visible in round-trip previews: {foreground}")
    if results["ROUNDTRIP"]["bounds"]["view_axis"] != "X":
        raise RuntimeError(f"thin-axis camera regression: {results['ROUNDTRIP']['bounds']}")
    contact_sheets = results["ROUNDTRIP"].get("contact_sheets", [])
    if len(contact_sheets) != 1:
        raise RuntimeError(f"ROUNDTRIP did not create one Action contact sheet: {contact_sheets}")
    contact_sheet = contact_sheets[0]
    if (contact_sheet.get("rows"), contact_sheet.get("columns")) != (2, 3):
        raise RuntimeError(f"five-point contact sheet is not 3x2: {contact_sheet}")
    if len(contact_sheet.get("cells", [])) != 5 or contact_sheet.get("frames") != [0, 1, 2, 3, 4]:
        raise RuntimeError(f"contact sheet cells do not match preview frames: {contact_sheet}")
    for key in ("path", "layout_path"):
        path = Path(contact_sheet[key])
        if not path.is_file() or not path.stat().st_size:
            raise RuntimeError(f"contact sheet output is missing or empty: {path}")
    if contact_sheet["size"][0] >= 5 * contact_sheet["tile_size"][0]:
        raise RuntimeError(f"contact sheet regressed to an unreadable single row: {contact_sheet['size']}")
    repeated_sheet = repeat.get("contact_sheets", [{}])[0]
    if repeated_sheet.get("pixel_sha256") != contact_sheet.get("pixel_sha256"):
        raise RuntimeError(f"repeated ROUNDTRIP produced stale or unstable contact sheet: {repeated_sheet}")
    if repeat["facts"].get("preview_pixel_hashes") != results["ROUNDTRIP"]["facts"].get("preview_pixel_hashes"):
        raise RuntimeError("repeated ROUNDTRIP changed decoded preview pixels")
    texture = results["EXPORT"]["textures"][0]
    conversion = texture.get("conversion", {})
    fidelity = conversion.get("fidelity") or {}
    if conversion.get("method") != "pillow_mediancut_rgba":
        raise RuntimeError(f"EXPORT bypassed the high-quality RGBA converter: {conversion}")
    if fidelity.get("orientation", {}).get("preferred") == "vertically_flipped":
        raise RuntimeError(f"EXPORT vertically flipped the Blender image: {fidelity}")
    if fidelity.get("mean_absolute_channel_error", 999) > 8 or fidelity.get("max_absolute_channel_error", 999) > 40:
        raise RuntimeError(f"EXPORT texture fidelity regression: {fidelity}")
    mesh_facts = results["PREFLIGHT"]["facts"]["meshes"]
    dimensions = mesh_facts[0].get("dimensions") if mesh_facts else None
    if not dimensions or any(
        abs(actual - expected) > 0.00001
        for actual, expected in zip(dimensions, [512.0, 64.0, 128.0])
    ):
        raise RuntimeError(f"preflight dimensions missing or incorrect: {mesh_facts}")
    summary = {
        "status": "pass",
        "stages": list(results),
        "mdl": str(artifacts / "extension_fixture.mdl"),
        "roundtrip_blend": results["ROUNDTRIP"]["blend"],
        "actions": results["ROUNDTRIP"]["facts"]["actions"],
        "preview_count": len(results["ROUNDTRIP"]["previews"]),
        "contact_sheet": contact_sheet["path"],
        "contact_sheet_layout": contact_sheet["layout_path"],
        "preview_foreground_min": min(foreground),
        "preview_foreground_max": max(foreground),
        "roundtrip_view_axis": results["ROUNDTRIP"]["bounds"]["view_axis"],
        "preflight_dimensions": mesh_facts[0]["dimensions"],
        "texture_conversion": conversion,
        "repeat_roundtrip": "pass",
        "repeat_names": repeated_names,
    }
    (artifacts / "fixture_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print("GOLDSRC_EXTENSION_FIXTURE", json.dumps(summary, sort_keys=True))
    return summary


if __name__ == "__main__":
    main()
