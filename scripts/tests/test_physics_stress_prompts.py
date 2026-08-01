from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
REFERENCE = REPO_ROOT / "docs" / "physics-stress-prompts.md"


class PhysicsStressPromptReferenceTests(unittest.TestCase):
    def test_reference_is_linked_and_contains_shared_contract(self) -> None:
        text = REFERENCE.read_text(encoding="utf-8")
        for required in (
            "$build-goldsrc-models",
            "Blender 5.2 LTS",
            "自包含 GoldSrc MDL",
            "不要用手工逐帧轨迹",
            "不规定",
        ):
            self.assertIn(required, text)

    def test_reference_covers_exportable_effect_families_and_pitfalls(self) -> None:
        text = REFERENCE.read_text(encoding="utf-8")
        for required in (
            "Rigid bodies",
            "Rope, chain, cable",
            "Cloth, banner, soft sheet",
            "Hinged doors and mechanisms",
            "Multi-stage fracture",
            "SourceIO-derived GoldSrc-only reader",
            "Bullet",
            "animation budgets",
        ):
            self.assertIn(required, text)

    def test_reference_contains_the_six_independent_prompts(self) -> None:
        text = REFERENCE.read_text(encoding="utf-8")
        headings = (
            "### 1. Pendulum Ball Breaks A Wall",
            "### 2. Suspended Bridge Collapse",
            "### 3. Cloth Awning Tears And Wraps",
            "### 4. Spring Latch And Hinged Door",
            "### 5. Mixed Objects On A Stepped Run",
            "### 6. Ceramic Pot Shatters On Steps",
        )
        positions = [text.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        self.assertGreaterEqual(text.count("Tests "), len(headings))


if __name__ == "__main__":
    unittest.main()
