from __future__ import annotations

import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[2]


class StaticQuickPathTests(unittest.TestCase):
    def test_main_skill_uses_progressive_static_workflow(self) -> None:
        path = SKILL_ROOT / "SKILL.md"
        text = path.read_text(encoding="utf-8")
        self.assertLessEqual(len(text.splitlines()), 150)
        self.assertIn("selected_static_export", text)
        self.assertIn("api.export_selected_static", text)
        self.assertNotIn("api.analyze_selected_static", text)
        self.assertNotIn("api.prepare_static_export", text)
        self.assertNotIn("api.execute_pipeline", text)
        self.assertIn('assurance="strict"', text)
        self.assertIn("static-api1-fallback.md", text)
        self.assertIn("Never manually repeat a passing stage", text)
        self.assertIn("sole exception", text)
        self.assertIn("evaluated_material_mapping_audit", text)

    def test_common_prompt_maps_to_one_explicit_product_call(self) -> None:
        text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(text.count("api.export_selected_static("), 1)
        self.assertIn("Map `2K` to `2048`", text)
        self.assertIn('"no baked lighting" to `unlit_color`', text)
        self.assertIn("agent decisions, not plugin defaults", text)

    def test_texture_roles_and_fallback_are_explicit(self) -> None:
        main = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        fallback = SKILL_ROOT / "references" / "static-api1-fallback.md"
        self.assertTrue(fallback.is_file())
        self.assertIn("logical PNG", main)
        self.assertIn("logical `.bmp`", main)
        self.assertIn("physical `512x512` indexed BMP tiles", main)
        self.assertIn("author_triangles", main)
        self.assertIn("crossed_tile_triangles", main)
        self.assertIn("post_tile_triangles", main)
        self.assertIn("Load this reference only when", fallback.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
