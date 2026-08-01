from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from goldsrc_toolchain.compatibility import (
    CompatibilityError,
    compare_model_compatibility,
    validate_player_portrait,
)
from goldsrc_toolchain.mdl_v10 import inspect_mdl
from goldsrc_toolchain.paths import resolve_toolchain


def inspection() -> dict:
    return {
        "bones": [
            {"index": 0, "name": "root", "parent": -1},
            {"index": 1, "name": "hand", "parent": 0},
        ],
        "sequences": [
            {
                "name": "idle", "fps": 30.0, "frame_count": 10, "blend_count": 1,
                "activity": 1, "activity_weight": 1, "events": [],
                "linear_movement": [0.0, 0.0, 0.0],
            },
            {
                "name": "walk", "fps": 24.0, "frame_count": 12, "blend_count": 2,
                "activity": 3, "activity_weight": 2,
                "events": [{"frame": 4, "id": 1003, "options": "open"}],
                "linear_movement": [16.0, 0.0, 0.0],
            },
        ],
        "hitboxes": [
            {"bone": 1, "group": 1, "min": [-1.0, -1.0, -1.0], "max": [1.0, 1.0, 1.0]},
        ],
        "skin_families": [[0]],
        "bodyparts": [{"name": "body", "model_count": 2}],
    }


class PlayerCompatibilityTests(unittest.TestCase):
    def test_local_sdk_barney_player_is_its_own_77_sequence_baseline(self) -> None:
        path = resolve_toolchain().official_player_mdl
        if path is None or not path.is_file():
            self.skipTest("Half-Life SDK Barney player baseline is not installed")
        parsed = inspect_mdl(path)
        self.assertEqual(len(parsed["sequences"]), 77)
        report = compare_model_compatibility(parsed, parsed, "player")
        self.assertEqual(report["status"], "pass", report)

    def test_accepts_baseline_and_terminal_appendage(self) -> None:
        baseline = inspection()
        candidate = copy.deepcopy(baseline)
        candidate["bones"].append({"index": 2, "name": "finger", "parent": 1})
        candidate["bones"].append({"index": 3, "name": "finger_tip", "parent": 2})
        report = compare_model_compatibility(candidate, baseline, "player")
        self.assertEqual(report["status"], "pass", report)

    def test_reports_blend_difference_without_claiming_authoring_support(self) -> None:
        baseline = inspection()
        candidate = copy.deepcopy(baseline)
        candidate["sequences"][1]["blend_count"] = 1
        report = compare_model_compatibility(candidate, baseline, "player")
        self.assertEqual(report["status"], "pass", report)
        self.assertTrue(any(item["code"] == "compat.sequence_blend_count" for item in report["issues"]))

    def test_rejects_sequence_order_fps_and_excess_frames(self) -> None:
        baseline = inspection()
        candidate = copy.deepcopy(baseline)
        candidate["sequences"].reverse()
        candidate["sequences"][0]["fps"] = 60
        candidate["sequences"][0]["frame_count"] = 99
        report = compare_model_compatibility(candidate, baseline, "player")
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any(item["code"] == "player.sequence_order" for item in report["issues"]))

        candidate = copy.deepcopy(baseline)
        candidate["sequences"][0]["fps"] = 60
        candidate["sequences"][1]["frame_count"] = 99
        report = compare_model_compatibility(candidate, baseline, "player")
        codes = {item["code"] for item in report["issues"]}
        self.assertIn("player.sequence_fps", codes)
        self.assertIn("player.sequence_frames", codes)

    def test_rejects_inserted_or_nonterminal_bones(self) -> None:
        baseline = inspection()
        inserted = copy.deepcopy(baseline)
        inserted["bones"].insert(1, {"index": 1, "name": "inserted", "parent": 0})
        inserted["bones"][2]["index"] = 2
        self.assertEqual(compare_model_compatibility(inserted, baseline, "player")["status"], "fail")

        nonterminal = copy.deepcopy(baseline)
        nonterminal["bones"].append({"index": 2, "name": "extra", "parent": 0})
        report = compare_model_compatibility(nonterminal, baseline, "player")
        self.assertTrue(any(item["code"] == "player.bone_appendage" for item in report["issues"]))

    def test_rejects_hitbox_skin_and_bodypart_changes(self) -> None:
        baseline = inspection()
        candidate = copy.deepcopy(baseline)
        candidate["hitboxes"][0]["group"] = 7
        candidate["skin_families"].append([0])
        candidate["bodyparts"] = [{"name": "equipment", "model_count": 3}]
        report = compare_model_compatibility(candidate, baseline, "player")
        codes = {item["code"] for item in report["issues"]}
        self.assertEqual(report["status"], "fail")
        self.assertTrue({"player.hitboxes", "player.skin_families", "player.bodypart"} <= codes)


class NpcCompatibilityTests(unittest.TestCase):
    def test_allows_appended_sequence_and_reports_metadata_differences(self) -> None:
        baseline = inspection()
        candidate = copy.deepcopy(baseline)
        candidate["sequences"][0]["activity_weight"] = 4
        candidate["sequences"].append({
            "name": "new_attack", "fps": 20.0, "frame_count": 8, "blend_count": 1,
            "activity": 5, "activity_weight": 1, "events": [],
            "linear_movement": [0.0, 0.0, 0.0],
        })
        report = compare_model_compatibility(candidate, baseline, "npc")
        self.assertEqual(report["status"], "pass", report)
        self.assertEqual(report["facts"]["appended_sequences"], ["new_attack"])
        self.assertTrue(any(item["field"] == "activity_weight" for item in report["differences"]))

    def test_rejects_reordered_or_inserted_sequence(self) -> None:
        baseline = inspection()
        candidate = copy.deepcopy(baseline)
        candidate["sequences"].insert(0, copy.deepcopy(candidate["sequences"][0]))
        candidate["sequences"][0]["name"] = "inserted"
        report = compare_model_compatibility(candidate, baseline, "npc")
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any(item["code"] == "npc.sequence_prefix" for item in report["issues"]))


class PortraitCompatibilityTests(unittest.TestCase):
    @staticmethod
    def _write(path: Path, size: tuple[int, int], colors: int) -> None:
        image = Image.new("P", size)
        image.putpalette([value for index in range(256) for value in (index, index, index)])
        image.putdata([index % colors for index in range(size[0] * size[1])])
        image.save(path, format="BMP")

    def test_accepts_164x200_indexed_portrait(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "portrait.bmp"
            self._write(path, (164, 200), 160)
            report = validate_player_portrait(path)
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["facts"]["used_color_count"], 160)

    def test_rejects_dimensions_or_too_many_nonremap_colors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wrong_size = root / "wrong_size.bmp"
            self._write(wrong_size, (160, 200), 16)
            with self.assertRaises(CompatibilityError):
                validate_player_portrait(wrong_size)
            too_many = root / "too_many.bmp"
            self._write(too_many, (164, 200), 161)
            with self.assertRaises(CompatibilityError):
                validate_player_portrait(too_many)
            self.assertEqual(validate_player_portrait(too_many, remapped=True)["status"], "pass")


if __name__ == "__main__":
    unittest.main()
