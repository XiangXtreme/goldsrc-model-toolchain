from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from goldsrc_toolchain.blender_preflight import inspect_scene
from test_toolchain import base_contract


class NamedCollection(list):
    def get(self, name):
        return next((item for item in self if item.name == name), None)


def fake_scene(*, frame_current=0, action_bound=True):
    material = SimpleNamespace(name="base.bmp", use_nodes=False)
    group = SimpleNamespace(index=0, name="root")
    membership = SimpleNamespace(group=0, weight=1.0)
    vertices = [SimpleNamespace(groups=[membership]) for _index in range(3)]
    polygon = SimpleNamespace(vertices=(0, 1, 2))
    mesh = SimpleNamespace(
        name="body_mesh",
        type="MESH",
        data=SimpleNamespace(
            vertices=vertices,
            polygons=[polygon],
            uv_layers=SimpleNamespace(active=object()),
        ),
        scale=(1.0, 1.0, 1.0),
        rotation_euler=(0.0, 0.0, 0.0),
        material_slots=[SimpleNamespace(material=material)],
        vertex_groups=[group],
    )
    action = SimpleNamespace(name="idle", frame_range=(0.0, 0.0))
    bone = SimpleNamespace(name="root", parent=None)
    armature = SimpleNamespace(
        name="rig",
        type="ARMATURE",
        data=SimpleNamespace(bones=[bone]),
        animation_data=SimpleNamespace(
            action=action if action_bound else None,
            nla_tracks=[],
        ),
    )
    scene = SimpleNamespace(
        objects=[mesh, armature],
        frame_start=0,
        frame_current=frame_current,
        frame_end=30,
    )
    bpy = SimpleNamespace(
        app=SimpleNamespace(version=(5, 2, 0)),
        context=SimpleNamespace(scene=scene),
        data=SimpleNamespace(
            objects=NamedCollection([mesh, armature]),
            materials=NamedCollection([material]),
            actions=[action],
        ),
    )
    return bpy


class BlenderPreflightTests(unittest.TestCase):
    def test_vertex_overflow_is_an_error_for_every_bundled_profile(self) -> None:
        for profile in ("half-life-cs", "sven-coop"):
            with self.subTest(profile=profile):
                contract = copy.deepcopy(base_contract())
                contract["target_profile"] = profile
                with mock.patch(
                    "goldsrc_toolchain.blender_preflight._evaluated_mesh_counts",
                    return_value=(2049, 1, 1),
                ):
                    report = inspect_scene(contract, bpy_module=fake_scene())
                budget = next(item for item in report["issues"] if item["code"] == "mesh.vertex_budget")
                self.assertEqual(report["status"], "fail")
                self.assertEqual(budget["severity"], "error")

    def test_playback_requires_bound_action_and_start_frame(self) -> None:
        report = inspect_scene(base_contract(), bpy_module=fake_scene(frame_current=12, action_bound=False))
        codes = {item["code"] for item in report["issues"]}
        self.assertIn("animation.playback_unbound", codes)
        self.assertIn("animation.playback_start", codes)
        self.assertFalse(report["facts"]["playback"]["ready"])


if __name__ == "__main__":
    unittest.main()
