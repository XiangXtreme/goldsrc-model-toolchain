"""Blender 5.2 regression fixture for a staged contact/fracture event chain."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import bpy
from mathutils import Vector


if "GOLDSRC_FIXTURE_ARTIFACTS" not in os.environ:
    raise RuntimeError("Set GOLDSRC_FIXTURE_ARTIFACTS to an absolute output directory")
if "GOLDSRC_TOOLCHAIN_ROOT" not in os.environ:
    raise RuntimeError("Set GOLDSRC_TOOLCHAIN_ROOT to the toolchain repository root")

OUT = Path(os.environ["GOLDSRC_FIXTURE_ARTIFACTS"]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(os.environ["GOLDSRC_TOOLCHAIN_ROOT"]).resolve() / "scripts"))

# A live MCP process may run this fixture more than once; do not retain an
# older toolchain module after a repository edit.
for module_name in tuple(sys.modules):
    if module_name == "goldsrc_toolchain" or module_name.startswith("goldsrc_toolchain."):
        sys.modules.pop(module_name, None)

from goldsrc_toolchain.physics_events import evaluate_event_chain
from goldsrc_toolchain.rigidbody_bake import _obb_sample


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for datablocks in (bpy.data.meshes, bpy.data.materials, bpy.data.cameras, bpy.data.lights):
        for datablock in list(datablocks):
            datablocks.remove(datablock)


def make_box(name: str, location: tuple[float, float, float], scale: tuple[float, float, float], color: tuple[float, float, float]) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(location=location)
    obj = bpy.context.object
    obj.name = name
    obj.scale = scale
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    material = bpy.data.materials.new(name + "_material")
    material.diffuse_color = (*color, 1.0)
    obj.data.materials.append(material)
    return obj


def key_path(obj: bpy.types.Object, positions: list[tuple[float, float, float]]) -> None:
    for frame, position in enumerate(positions):
        obj.location = position
        obj.keyframe_insert(data_path="location", frame=frame)
    action = obj.animation_data.action if obj.animation_data else None
    # Blender 5.2 uses layered Actions without the legacy ``fcurves`` property.
    # Integer-frame sampling remains deterministic, so only adjust interpolation
    # when the legacy collection is available.
    for curve in getattr(action, "fcurves", ()):
        for point in curve.keyframe_points:
            point.interpolation = "LINEAR"


def render_frame(scene: bpy.types.Scene, frame: int, path: Path) -> None:
    scene.frame_set(frame)
    scene.view_layers[0].update()
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


def run() -> dict:
    clear_scene()
    scene = bpy.context.scene
    scene.frame_start = 0
    scene.frame_end = 8
    scene.render.fps = 30
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 640
    scene.render.resolution_y = 480
    scene.render.resolution_percentage = 100
    scene.world.color = (0.025, 0.025, 0.03)

    ground = make_box("ground", (0.0, 0.0, -0.6), (5.0, 3.0, 0.5), (0.18, 0.18, 0.2))
    wall = make_box("wall", (0.0, 0.0, 1.0), (0.5, 2.0, 1.0), (0.45, 0.12, 0.05))
    rock = make_box("rock", (-3.0, 0.0, 1.0), (0.45, 0.45, 0.45), (0.28, 0.32, 0.36))
    fragment = make_box("fragment", (0.0, 0.0, 1.0), (0.35, 0.35, 0.35), (0.62, 0.22, 0.06))
    key_path(rock, [(-3.0, 0.0, 1.0), (-2.0, 0.0, 1.0), (-1.0, 0.0, 1.0), (-0.4, 0.0, 1.0), (-0.2, 0.8, 1.0), (0.0, 1.6, 1.0), (0.0, 2.3, 0.8), (0.0, 2.8, 0.5), (0.0, 3.0, 0.45)])
    key_path(fragment, [(0.0, 0.0, 1.0), (0.0, 0.0, 1.0), (0.0, 0.0, 1.0), (0.0, 0.0, 1.0), (0.0, 0.0, 1.5), (0.4, 0.3, 1.8), (0.8, 0.7, 1.4), (1.0, 1.0, 0.8), (1.2, 1.2, 0.45)])

    bpy.ops.object.camera_add(location=(7.5, -10.0, 7.0))
    camera = bpy.context.object
    camera.data.lens = 52
    camera.rotation_euler = (Vector((0.0, 0.5, 1.0)) - camera.location).to_track_quat("-Z", "Y").to_euler()
    scene.camera = camera
    bpy.ops.object.light_add(type="AREA", location=(2.0, -4.0, 8.0))
    bpy.context.object.data.energy = 1100
    bpy.context.object.data.size = 5.0
    bpy.ops.object.light_add(type="AREA", location=(-4.0, 3.0, 4.0))
    bpy.context.object.data.energy = 500
    bpy.context.object.data.size = 4.0

    samples = {}
    objects = [rock, wall, fragment]
    previous = {}
    for frame in range(scene.frame_start, scene.frame_end + 1):
        scene.frame_set(frame)
        scene.view_layers[0].update()
        samples[frame] = {}
        for obj in objects:
            matrix = obj.matrix_world.copy()
            rotation_delta = 0.0
            if obj.name in previous:
                rotation_delta = previous[obj.name].to_quaternion().rotation_difference(matrix.to_quaternion()).angle
            samples[frame][obj.name] = _obb_sample(obj, matrix, rotation_delta)
            previous[obj.name] = matrix

    physics = {
        "mode": "baked_event_chain",
        "simulation": {
            "source_fps": 30,
            "sample_step": 1,
            "export_fps": 30,
            "sequence": "break",
            "max_frame": 8,
            "contact_margin": 0.02,
            "translation_epsilon": 0.01,
            "rotation_epsilon": 0.01,
        },
        "stages": [
            {"name": "impact", "trigger": {"type": "frame", "frame": 0}, "release": ["rock"], "expected_motion_window": [1, 3]},
            {
                "name": "fracture",
                "depends_on": ["impact"],
                "trigger": {"type": "contact", "pair": ["rock", "wall"], "offset_frames": 1, "window": [1, 5]},
                "release": ["fragment"],
                "must_be_still_before": ["fragment"],
                "expected_motion_window": [4, 6],
            },
        ],
        "interactions": [{"name": "rock_deflects", "pair": ["rock", "wall"], "window": [1, 5], "response": "deflect"}],
    }
    report = evaluate_event_chain(physics, samples, final_report={"settled": True, "kinematic_at_end": [], "potentially_unwoken": [], "outside_receiver": []})
    if report["status"] != "pass":
        raise RuntimeError(json.dumps(report, ensure_ascii=False))

    for frame in (0, 3, 5, 8):
        render_frame(scene, frame, OUT / f"event_chain_frame_{frame:03d}.png")
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "rigidbody_event_chain.blend"))
    output = {"status": report["status"], "physics_event_report": report, "frames": [0, 3, 5, 8], "blender": bpy.app.version_string, "objects": [obj.name for obj in objects], "ground": ground.name}
    (OUT / "rigidbody_event_chain.json").write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, ensure_ascii=False))
