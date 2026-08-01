from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from goldsrc_toolchain.blender_namespace import (
    assert_exact_asset_namespace,
    inspect_asset_namespace,
    purge_asset_namespace,
)


class FakeCollection(list):
    def remove(self, item, *, do_unlink=False):
        super().remove(item)


def item(name: str, *, rigid_body=None, rigid_body_constraint=None):
    return SimpleNamespace(
        name=name,
        use_fake_user=True,
        rigid_body=rigid_body,
        rigid_body_constraint=rigid_body_constraint,
    )


def fake_bpy(**collections):
    values = {
        kind: FakeCollection(collections.get(kind, []))
        for kind in ("objects", "meshes", "curves", "armatures", "materials", "images", "actions")
    }
    return SimpleNamespace(data=SimpleNamespace(**values))


class BlenderNamespaceTests(unittest.TestCase):
    def test_purge_removes_only_exact_and_numeric_suffixes(self) -> None:
        bpy = fake_bpy(
            objects=[item("Bridge"), item("Bridge.001"), item("Bridge.1000"), item("BridgeExtra")],
            meshes=[item("BridgeMesh"), item("BridgeMesh.004"), item("OtherMesh")],
        )
        report = purge_asset_namespace(
            {"objects": ["Bridge"], "meshes": ["BridgeMesh"]},
            bpy_module=bpy,
        )
        self.assertEqual(report["removed"]["objects"], ["Bridge", "Bridge.001", "Bridge.1000"])
        self.assertEqual([value.name for value in bpy.data.objects], ["BridgeExtra"])
        self.assertEqual([value.name for value in bpy.data.meshes], ["OtherMesh"])

    def test_refuses_to_remove_bullet_owned_objects(self) -> None:
        bpy = fake_bpy(objects=[item("Shard", rigid_body=object())])
        with self.assertRaisesRegex(RuntimeError, "Bullet-owned"):
            purge_asset_namespace({"objects": ["Shard"]}, bpy_module=bpy)
        self.assertEqual([value.name for value in bpy.data.objects], ["Shard"])

    def test_exact_assertion_reports_missing_and_suffix_collisions(self) -> None:
        bpy = fake_bpy(materials=[item("Wood"), item("Wood.001")])
        facts = inspect_asset_namespace({"materials": ["Wood"]}, bpy_module=bpy)
        self.assertEqual(facts["datablocks"]["materials"]["Wood"]["suffixes"], ["Wood.001"])
        with self.assertRaisesRegex(RuntimeError, "suffixed collisions"):
            assert_exact_asset_namespace({"materials": ["Wood"]}, bpy_module=bpy)
        with self.assertRaisesRegex(RuntimeError, "is missing"):
            assert_exact_asset_namespace({"actions": ["Swing"]}, bpy_module=bpy)


if __name__ == "__main__":
    unittest.main()
