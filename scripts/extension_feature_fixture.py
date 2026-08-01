"""Exercise bodygroups, skins, special flags and rigid-body API in Blender 5.2."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import bpy


def _reset() -> None:
    if bpy.context.scene.rigidbody_world is not None:
        for obj in list(bpy.context.scene.objects):
            if obj.rigid_body_constraint:
                bpy.context.view_layer.objects.active = obj
                bpy.ops.rigidbody.constraint_remove()
        for obj in list(bpy.context.scene.objects):
            if obj.rigid_body:
                bpy.context.view_layer.objects.active = obj
                bpy.ops.rigidbody.object_remove()
        bpy.context.scene.collection.children.unlink(bpy.context.scene.rigidbody_world.collection)
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)
    for datablocks in (bpy.data.meshes, bpy.data.armatures, bpy.data.materials, bpy.data.images):
        for datablock in list(datablocks):
            if datablock.users == 0:
                datablocks.remove(datablock)


def _rig():
    data = bpy.data.armatures.new("features_ARM_DATA")
    armature = bpy.data.objects.new("features_ARM", data)
    bpy.context.scene.collection.objects.link(armature)
    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    root = data.edit_bones.new("root")
    root.head = (0.0, 0.0, 0.0)
    root.tail = (0.0, 0.0, 1.0)
    control = data.edit_bones.new("control")
    control.head = (0.0, 0.0, 1.0)
    control.tail = (0.0, 0.0, 2.0)
    control.parent = root
    bpy.ops.object.mode_set(mode="OBJECT")
    action = bpy.data.actions.new("feature_idle")
    layer = action.layers.new("Layer")
    strip = layer.strips.new()
    slot = action.slots.new("OBJECT", armature.name)
    bag = strip.channelbags.new(slot)
    curve = bag.fcurves.new('pose.bones["root"].location', index=0)
    curve.keyframe_points.add(1)
    curve.keyframe_points[0].co = (0, 0)
    curve.update()
    armature.animation_data_create()
    armature.animation_data.action = action
    armature.animation_data.action_slot = slot
    return armature


def _image(token: str, first, second, *, masked=False):
    image = bpy.data.images.new(token, 64, 64, alpha=True)
    pixels = []
    for y in range(64):
        for x in range(64):
            color = first if ((x // 8 + y // 8) % 2) else second
            alpha = 0.0 if masked and x < 8 and y < 8 else 1.0
            pixels.extend((*color, alpha))
    image.pixels.foreach_set(pixels)
    image.update()
    material = bpy.data.materials.new(token)
    material.use_nodes = True
    node = material.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = image
    material["goldsrc_texture_token"] = token
    return material


def _object(name: str, vertices, faces, materials, armature):
    mesh = bpy.data.meshes.new(name + "_MESH")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    uv = mesh.uv_layers.new(name="UVMap")
    for polygon in mesh.polygons:
        polygon.material_index = polygon.index % len(materials)
        for offset, loop_index in enumerate(polygon.loop_indices):
            uv.data[loop_index].uv = ((offset == 1), (offset == 2))
    for material in materials:
        mesh.materials.append(material)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.parent = armature
    modifier = obj.modifiers.new("Skeleton", "ARMATURE")
    modifier.object = armature
    group = obj.vertex_groups.new(name="root")
    group.add(list(range(len(mesh.vertices))), 1.0, "REPLACE")
    return obj


def _feature_scene():
    _reset()
    scene = bpy.context.scene
    scene.frame_start = 0
    scene.frame_end = 0
    scene.frame_set(0)
    armature = _rig()
    skin_a = _image("skin_a.bmp", (0.75, 0.12, 0.08), (0.18, 0.03, 0.02), masked=True)
    metal = _image("metal.bmp", (0.7, 0.72, 0.76), (0.12, 0.14, 0.17))
    skin_b = _image("skin_b.bmp", (0.05, 0.5, 0.74), (0.02, 0.08, 0.18))
    metal_alt = _image("metal_alt.bmp", (0.65, 0.55, 0.12), (0.14, 0.1, 0.02))
    base = _object(
        "base_mesh",
        [(-1, -0.5, 0), (1, -0.5, 0), (1, 0.5, 0), (-1, 0.5, 0), (0, 0, 1.4)],
        [(0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4), (0, 3, 2), (0, 2, 1)],
        [skin_a, metal], armature,
    )
    base.vertex_groups["root"].remove([4])
    control_group = base.vertex_groups.new(name="control")
    control_group.add([4], 1.0, "REPLACE")
    hat_a = _object("hat_a", [(-0.6, -0.3, 1.3), (0.6, -0.3, 1.3), (0, 0.3, 2.0)], [(0, 1, 2)], [skin_a], armature)
    hat_b = _object("hat_b", [(-0.8, -0.4, 1.3), (0.8, -0.4, 1.3), (0.8, 0.4, 1.3), (-0.8, 0.4, 1.3)], [(0, 1, 2), (0, 2, 3)], [metal], armature)
    return base, hat_a, hat_b


def _contract() -> dict:
    return {
        "version": 2,
        "intent": {
            "request": "Build one bodygroup and skin-family validation model.",
            "requirements": [{
                "id": "feature-model", "source": "bodygroup and skin-family validation model",
                "evidence_phases": ["preflight", "export", "compile_sven", "mdl_inspect", "sourceio_roundtrip"],
            }],
            "assumptions": [],
        },
        "target_profile": "sven-coop",
        "model_name": "extension_features.mdl",
        "scale": 1.0,
        "bones": [{"name": "root", "parent": None}, {"name": "control", "parent": "root"}],
        "bodies": [{"name": "base", "source": "base.smd", "object": "base_mesh"}],
        "bodygroups": [{
            "name": "hat",
            "choices": [
                {"studio": "hat_a.smd", "object": "hat_a"},
                {"studio": "hat_b.smd", "object": "hat_b"},
                {"blank": True},
            ],
        }],
        "textures": [
            {"name": "skin_a.bmp", "source": "skin_a.bmp", "width": 64, "height": 64, "modes": ["masked"]},
            {"name": "metal.bmp", "source": "metal.bmp", "width": 64, "height": 64, "modes": ["chrome"]},
            {"name": "skin_b.bmp", "source": "skin_b.bmp", "width": 64, "height": 64, "modes": ["additive"]},
            {"name": "metal_alt.bmp", "source": "metal_alt.bmp", "width": 64, "height": 64, "modes": ["fullbright", "nomips"]},
        ],
        "skin_families": [["skin_a.bmp", "metal.bmp"], ["skin_b.bmp", "metal_alt.bmp"]],
        "sequences": [{
            "name": "idle", "source": "feature_idle.smd", "action": "feature_idle",
            "fps": 12, "frame": [0, 0], "loop": True, "events": [], "motion": [],
        }],
        "hitboxes": [{"group": 0, "bone": "root", "min": [-1, -1, 0], "max": [1, 1, 2]}],
        "attachments": [{"index": 0, "bone": "root", "origin": [0, 0, 1.8]}],
        "controllers": [{"index": 0, "bone": "control", "type": "XR", "start": -45, "end": 45}],
        "bounds": {
            "bbox": {"min": [-1.2, -1, 0], "max": [1.2, 1, 2.2]},
            "cbox": {"min": [-1.2, -1, 0], "max": [1.2, 1, 2.2]},
        },
        "acceptance": {
            "required_phases": ["preflight", "export", "compile_sven", "mdl_inspect", "sourceio_roundtrip"],
            "visual_views": ["three_quarter"], "allow_known_blockers": [],
        },
    }


def _run_features(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    _feature_scene()
    contract = root / "model_contract.json"
    contract.write_text(json.dumps(_contract(), indent=2) + "\n", encoding="utf-8")
    reports = {}
    for stage, filename in (
        ("PREFLIGHT", "preflight.json"), ("EXPORT", "export.json"),
        ("COMPILE", "compile_sven.json"), ("INSPECT", "mdl_inspection.json"),
        ("ROUNDTRIP", "sourceio_roundtrip.json"),
    ):
        result = bpy.ops.goldsrc_toolchain.execute_stage(
            stage=stage, contract_path=str(contract), artifacts_dir=str(root), report_path=filename,
        )
        report = json.loads((root / filename).read_text(encoding="utf-8"))
        if result != {"FINISHED"} or report.get("status") != "pass":
            raise RuntimeError(f"feature {stage} failed: {result} {report}")
        reports[stage] = report
    inspection = reports["INSPECT"]["inspections"]["sven"]
    return {
        "status": "pass",
        "bodyparts": [item["name"] for item in inspection["bodyparts"]],
        "skin_families": inspection["skin_families"],
        "texture_flags": {item["name"]: item["flag_names"] for item in inspection["textures"]},
        "controllers": len(inspection["controllers"]),
        "attachments": len(inspection["attachments"]),
        "actions": reports["ROUNDTRIP"]["facts"]["actions"],
    }


def _cube(name, location, size):
    bpy.ops.mesh.primitive_cube_add(location=location, scale=(size, size, size))
    obj = bpy.context.object
    obj.name = name
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return obj


def _rigidbody(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    _reset()
    scene = bpy.context.scene
    ground = _cube("ground", (0, 0, -0.5), 3.0)
    ground.scale.z = 0.15
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    bpy.context.view_layer.objects.active = ground
    bpy.ops.rigidbody.object_add()
    ground.rigid_body.type = "PASSIVE"
    falling = _cube("falling", (0, 0, 3.0), 0.5)
    bpy.context.view_layer.objects.active = falling
    bpy.ops.rigidbody.object_add()
    falling.rigid_body.type = "ACTIVE"
    falling.rigid_body.use_deactivation = True
    api = bpy.app.driver_namespace["goldsrc_model_toolchain"]
    world = api.configure_rigidbody_world(
        scene, frame_start=1, frame_end=180, substeps_per_frame=8, solver_iterations=20, time_scale=1.0,
    )
    module_name = next(name for name in bpy.context.preferences.addons.keys() if name.endswith(".goldsrc_model_toolchain"))
    bake_module = importlib.import_module(module_name + ".core.rigidbody_bake")
    config = bake_module.SettlementConfig(
        frame_start=1, max_frame=180, activity_after_frame=2,
        translation_epsilon=0.0005, rotation_epsilon=0.0005,
        consecutive_frames=12, hold_frames=3,
        receiver_bounds=(-4, -4, -1, 4, 4, 4),
    )
    bake = api.bake_rigidbody(scene, [falling], config)
    if not bake["report"]["settled"] or bake["report"]["outside_receiver"]:
        raise RuntimeError(f"rigid-body fixture did not settle: {bake}")
    matrices = api.capture_matrices(bake["capture_id"])
    json.dumps(matrices)
    matrix_file = api.write_capture_matrices(bake["capture_id"], root / "capture_matrices.json")
    release = api.release_rigidbody_capture(bake["capture_id"])
    report = {
        "status": "pass", "world": world, "bake": bake,
        "matrix_frames": len(matrices["matrices"]),
        "matrix_file": matrix_file,
        "capture_release": release,
    }
    (root / "rigidbody_api.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> dict:
    root = Path(os.environ["GOLDSRC_EXTENSION_FEATURE_FIXTURE"]).expanduser().resolve()
    summary = {
        "status": "pass",
        "features": _run_features(root / "features"),
        "rigidbody": _rigidbody(root / "physics"),
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "feature_fixture_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print("GOLDSRC_EXTENSION_FEATURE_FIXTURE", json.dumps(summary, sort_keys=True))
    return summary


if __name__ == "__main__":
    main()
