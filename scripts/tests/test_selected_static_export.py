from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from goldsrc_toolchain.errors import ToolchainError
from goldsrc_toolchain.selected_static_export import run_selected_static_export


def _analysis() -> dict:
    return {
        "status": "pass",
        "analysis_id": "analysis-1",
        "summary": {
            "object": "StaticMesh",
            "evaluated_vertices": 12,
            "evaluated_triangles": 20,
        },
    }


def _prepared() -> dict:
    return {
        "status": "pass",
        "analysis_id": "analysis-1",
        "contract_path": "contract.json",
        "artifacts_dir": "artifacts",
        "author_checkpoint": "author.blend",
        "facts": {"texture_size": 2048},
    }


class SelectedStaticExportTests(unittest.TestCase):
    def test_failed_analysis_stops_before_preparation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = run_selected_static_export(
                artifacts_dir=temporary,
                analyze=lambda: {"status": "fail"},
                prepare=lambda _analysis_result: self.fail("prepare must not run"),
                execute_pipeline=lambda _prepared_result: self.fail("pipeline must not run"),
            )
        self.assertEqual(result["failed_stage"], "ANALYZE")
        self.assertEqual(result["error"]["code"], "static_export.analysis_status")

    def test_success_runs_one_ordered_workflow_and_returns_only_delivery_summary(self) -> None:
        calls = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def analyze():
                calls.append("analyze")
                return _analysis()

            def prepare(analysis):
                calls.append(("prepare", analysis["analysis_id"]))
                return _prepared()

            def pipeline(prepared):
                calls.append(("pipeline", prepared["contract_path"]))
                return {
                    "status": "pass",
                    "mdl": str(root / "compiled.mdl"),
                    "report_directory": str(root / "reports"),
                    "warnings": [],
                    "facts": {"triangles": 936, "compiled_tiles": 13, "textures": 13},
                    "stages": {"EXPORT": {"large": list(range(1000))}},
                    "delivery": {
                        "path": str(root / "delivery" / "model.mdl"),
                        "sha256": "abc",
                        "bytes": 123,
                    },
                }

            result = run_selected_static_export(
                artifacts_dir=root,
                analyze=analyze,
                prepare=prepare,
                execute_pipeline=pipeline,
            )

            self.assertEqual(calls, ["analyze", ("prepare", "analysis-1"), ("pipeline", "contract.json")])
            self.assertEqual(
                set(result), {"status", "mdl", "report_directory", "warnings", "facts"},
            )
            self.assertEqual(result["mdl"], str(root / "delivery" / "model.mdl"))
            self.assertEqual(result["facts"]["compiled_tiles"], 13)
            self.assertNotIn("stages", result)
            self.assertLess(len(json.dumps(result).encode("utf-8")), 16 * 1024)
            report = json.loads((root / "reports" / "static_export.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["workflow"], "selected_static_export")
            self.assertNotIn("stages", report)

    def test_needs_decision_stops_before_pipeline_without_expanding_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pipeline_called = False

            def pipeline(_prepared):
                nonlocal pipeline_called
                pipeline_called = True
                return {}

            result = run_selected_static_export(
                artifacts_dir=root,
                analyze=_analysis,
                prepare=lambda _analysis_result: {
                    "status": "needs_decision",
                    "analysis_id": "analysis-1",
                    "decisions": [{"parameter": "uv_strategy", "options": ["existing", "smart_project"]}],
                },
                execute_pipeline=pipeline,
            )

            self.assertFalse(pipeline_called)
            self.assertEqual(result["status"], "needs_decision")
            self.assertEqual(result["facts"]["object"], "StaticMesh")
            self.assertEqual(result["decisions"][0]["parameter"], "uv_strategy")

    def test_prepare_exception_is_compact_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def prepare(_analysis_result):
                raise ToolchainError(
                    "PREPARE", "static.material", "Unsupported material",
                    {"large": list(range(1000))},
                )

            result = run_selected_static_export(
                artifacts_dir=root,
                analyze=_analysis,
                prepare=prepare,
                execute_pipeline=lambda _prepared_result: self.fail("pipeline must not run"),
            )

            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["failed_stage"], "PREPARE")
            self.assertEqual(
                result["error"],
                {"phase": "PREPARE", "code": "static.material", "message": "Unsupported material"},
            )
            persisted = json.loads((root / "reports" / "static_export.json").read_text(encoding="utf-8"))
            self.assertIn("large", persisted["evidence"]["full_error"]["details"])

    def test_pipeline_failure_propagates_owning_stage_without_stage_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = run_selected_static_export(
                artifacts_dir=root,
                analyze=_analysis,
                prepare=lambda _analysis_result: _prepared(),
                execute_pipeline=lambda _prepared_result: {
                    "status": "fail",
                    "mdl": None,
                    "report_directory": str(root / "reports"),
                    "warnings": [],
                    "facts": {"triangles": 720},
                    "failed_stage": "EXPORT",
                    "error": {
                        "phase": "EXPORT",
                        "code": "fixture.export",
                        "message": "Export failed",
                        "details": {"large": list(range(1000))},
                    },
                    "stages": {"EXPORT": {"large": list(range(1000))}},
                },
            )

            self.assertEqual(result["failed_stage"], "EXPORT")
            self.assertEqual(result["error"]["code"], "fixture.export")
            self.assertNotIn("details", result["error"])
            self.assertNotIn("stages", result)


if __name__ == "__main__":
    unittest.main()
