"""Clean-profile end-to-end fixture for the GoldSrc Extension stages."""

from __future__ import annotations

import json
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
    curve = bag.fcurves.new('pose.bones["root"].location', index=0)
    curve.keyframe_points.add(5)
    for point, value in zip(curve.keyframe_points, (0.0, 0.3, 0.75, 0.3, 0.0)):
        point.co = (point.co.x, value)
    for index, point in enumerate(curve.keyframe_points):
        point.co.x = index
        point.interpolation = "BEZIER"
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
        [(-0.5, -0.5, 0.0), (0.5, -0.5, 0.0), (0.5, 0.5, 0.0), (-0.5, 0.5, 0.0), (0.0, 0.0, 1.0)],
        [],
        [(0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4), (0, 3, 2), (0, 2, 1)],
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
            checker = ((x // 8) + (y // 8)) % 2
            pixels.extend((0.12 + checker * 0.55, 0.3 + checker * 0.35, 0.75 - checker * 0.45, 1.0))
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
        "hitboxes": [{"group": 0, "bone": "root", "min": [-1, -1, -0.1], "max": [1, 1, 1.2]}],
        "attachments": [],
        "controllers": [],
        "bounds": {
            "bbox": {"min": [-1, -1, -0.1], "max": [2, 1, 1.2]},
            "cbox": {"min": [-1, -1, -0.1], "max": [2, 1, 1.2]},
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
    summary = {
        "status": "pass",
        "stages": list(results),
        "mdl": str(artifacts / "extension_fixture.mdl"),
        "roundtrip_blend": results["ROUNDTRIP"]["blend"],
        "actions": results["ROUNDTRIP"]["facts"]["actions"],
        "preview_count": len(results["ROUNDTRIP"]["previews"]),
        "preview_foreground_min": min(foreground),
        "preview_foreground_max": max(foreground),
        "repeat_roundtrip": "pass",
        "repeat_names": repeated_names,
    }
    (artifacts / "fixture_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print("GOLDSRC_EXTENSION_FIXTURE", json.dumps(summary, sort_keys=True))
    return summary


if __name__ == "__main__":
    main()
