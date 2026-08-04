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
