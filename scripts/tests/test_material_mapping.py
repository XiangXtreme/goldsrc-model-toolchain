from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


SCRIPT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_ROOT))

from goldsrc_toolchain.material_mapping import (
    aggregate_token_triangles,
    distribution_projection,
    inspect_mesh_material_usage,
    mesh_geometry_signature,
    mesh_material_assignment_signature,
)


class _Material:
    def __init__(self, name: str, pointer: int, token: str | None = None) -> None:
        self.name = name
        self.name_full = name
        self.library = None
        self._pointer = pointer
        self._token = token

    def as_pointer(self) -> int:
        return self._pointer

    def get(self, name: str):
        return self._token if name == "goldsrc_texture_token" else None


class MaterialMappingTests(unittest.TestCase):
    def test_geometry_signature_detects_positions_and_face_winding(self) -> None:
        vertices = [
            SimpleNamespace(co=(0.0, 0.0, 0.0)),
            SimpleNamespace(co=(1.0, 0.0, 0.0)),
            SimpleNamespace(co=(0.0, 1.0, 0.0)),
        ]
        mesh = SimpleNamespace(
            vertices=vertices, loops=[object(), object(), object()],
            polygons=[SimpleNamespace(vertices=(0, 1, 2))],
        )
        baseline = mesh_geometry_signature(mesh)
        vertices[0].co = (0.0, 0.0, 0.25)
        self.assertNotEqual(mesh_geometry_signature(mesh), baseline)
        vertices[0].co = (0.0, 0.0, 0.0)
        mesh.polygons[0].vertices = (0, 2, 1)
        self.assertNotEqual(mesh_geometry_signature(mesh), baseline)

    def test_face_material_signature_detects_swaps_with_equal_counts(self) -> None:
        first = _Material("First", 1)
        second = _Material("Second", 2)
        polygons = [
            SimpleNamespace(material_index=0, vertices=(0, 1, 2)),
            SimpleNamespace(material_index=1, vertices=(2, 3, 0)),
        ]
        mesh = SimpleNamespace(materials=[first, second], polygons=polygons)
        baseline_usage = inspect_mesh_material_usage(mesh)
        baseline = mesh_material_assignment_signature(mesh, usage=baseline_usage)
        polygons[0].material_index = 1
        polygons[1].material_index = 0
        swapped_usage = inspect_mesh_material_usage(mesh)
        self.assertEqual(
            [item["faces"] for item in baseline_usage.distribution],
            [item["faces"] for item in swapped_usage.distribution],
        )
        self.assertNotEqual(
            mesh_material_assignment_signature(mesh, usage=swapped_usage), baseline,
        )
    def test_reports_unused_raw_slot_and_used_evaluated_slot_separately(self) -> None:
        mesh = SimpleNamespace(
            materials=[_Material("CaveRock_Mat", 1), _Material("AutoTerrain_base", 2)],
            polygons=[
                SimpleNamespace(material_index=1, vertices=(0, 1, 2, 3)),
                SimpleNamespace(material_index=1, vertices=(4, 5, 6)),
            ],
        )
        usage = inspect_mesh_material_usage(mesh)
        self.assertEqual(usage.polygon_indices, (1, 1))
        self.assertEqual(usage.triangles, 3)
        self.assertEqual(
            [(item["slot"], item["faces"], item["triangles"], item["used"]) for item in usage.distribution],
            [(0, 0, 0, False), (1, 2, 3, True)],
        )

    def test_projection_and_token_aggregation_are_stable(self) -> None:
        distribution = [
            {"slot": 1, "material": {"name": "B", "library": None}, "token": "b.bmp", "faces": 2, "triangles": 3},
            {"slot": 0, "material": {"name": "A", "library": None}, "token": "a.bmp", "faces": 1, "triangles": 2},
            {"slot": 2, "material": {"name": "A", "library": None}, "token": "a.bmp", "faces": 4, "triangles": 5},
        ]
        projected = distribution_projection(
            distribution, include_material=False, include_token=True,
        )
        self.assertEqual([item["slot"] for item in projected], [0, 1, 2])
        self.assertEqual(
            aggregate_token_triangles(distribution),
            {"a.bmp": 7, "b.bmp": 3},
        )

    def test_invalid_polygon_material_index_is_reported(self) -> None:
        mesh = SimpleNamespace(
            materials=[_Material("Only", 1)],
            polygons=[SimpleNamespace(material_index=4, vertices=(0, 1, 2))],
        )
        usage = inspect_mesh_material_usage(mesh)
        self.assertEqual(usage.invalid_indices, (4,))
        self.assertEqual(usage.triangles, 0)


if __name__ == "__main__":
    unittest.main()
