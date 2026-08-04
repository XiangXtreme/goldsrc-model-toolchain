from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from goldsrc_toolchain.errors import ToolchainError
from goldsrc_toolchain.reporting import (
    failure_report,
    requirement_report_reference,
    resolve_report_path,
    summarize_stage_report,
    write_json,
)


class RuntimeReportingTests(unittest.TestCase):
    def test_canonical_and_custom_report_paths_cannot_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(
                resolve_report_path(root, stage="EXPORT"),
                root.resolve() / "reports" / "export.json",
            )
            self.assertEqual(
                resolve_report_path(root, stage="EXPORT", report_path="custom/export.json"),
                root.resolve() / "custom" / "export.json",
            )
            with self.assertRaisesRegex(ToolchainError, "inside artifacts_dir"):
                resolve_report_path(root, stage="EXPORT", report_path="../escape.json")

    def test_atomic_write_leaves_only_the_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "reports" / "result.json"
            write_json(destination, {"status": "pass", "value": 1})
            write_json(destination, {"status": "pass", "value": 2})
            self.assertEqual(json.loads(destination.read_text(encoding="utf-8"))["value"], 2)
            self.assertEqual([path.name for path in destination.parent.iterdir()], ["result.json"])

    def test_failure_report_and_requirement_reference_are_compact(self) -> None:
        error = ToolchainError("EXPORT", "fixture.failure", "failed", {"large": list(range(20))})
        report = failure_report(error, stage="EXPORT")
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["error"]["code"], "fixture.failure")
        self.assertEqual(requirement_report_reference(), {"report_section": "/facts"})

    def test_sixteen_tile_export_summary_is_under_16_kib(self) -> None:
        tiles = []
        for index in range(16):
            tiles.append({
                "name": f"atlas_{index:02d}.bmp",
                "conversion": {
                    "indices_used": list(range(256)),
                    "pixel_frequencies": {str(value): value * 100 for value in range(256)},
                    "palette": [[value, value, value] for value in range(256)],
                    "fidelity": {
                        "mean_absolute_channel_error": 2.5,
                        "max_absolute_channel_error": 12,
                    },
                },
            })
        report = {
            "status": "pass",
            "phase": "export",
            "issues": [],
            "known_blockers": [],
            "references": [{
                "triangles": 720,
                "static_material_audit": {
                    "status": "pass",
                    "source_object": "source",
                    "prepared_object": "prepared",
                    "smd_logical_token_triangles": {"atlas.bmp": 720},
                },
                "prepared": {
                    "compiled_sources": ["reference.smd"],
                    "triangles": 936,
                    "large_texture_tiling": [{"crossed_triangles": 108}],
                },
            }],
            "texture_selection": {
                "compiled": [item["name"] for item in tiles[:13]],
                "omitted_unused_large_tiles": [item["name"] for item in tiles[13:]],
            },
            "textures": tiles,
            "requirement_evidence": [{
                "id": "static-export", "status": "pass",
                "evidence": {"report_section": "/facts"},
            }],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "reports" / "export.json"
            summary = summarize_stage_report("EXPORT", report, path)
        encoded = json.dumps(summary, ensure_ascii=False).encode("utf-8")
        self.assertLess(len(encoded), 16 * 1024)
        self.assertNotIn(b"indices_used", encoded)
        self.assertNotIn(b"pixel_frequencies", encoded)
        self.assertNotIn(b"palette", encoded)
        self.assertEqual(summary["facts"]["source_triangles"], 720)
        self.assertEqual(summary["facts"]["output_triangles"], 936)
        self.assertEqual(summary["facts"]["author_triangles"], 720)
        self.assertEqual(summary["facts"]["crossed_tile_triangles"], 108)
        self.assertEqual(summary["facts"]["post_tile_triangles"], 936)
        self.assertEqual(summary["facts"]["compiled_textures"], 13)
        self.assertEqual(summary["facts"]["material_audits"][0]["status"], "pass")


if __name__ == "__main__":
    unittest.main()
