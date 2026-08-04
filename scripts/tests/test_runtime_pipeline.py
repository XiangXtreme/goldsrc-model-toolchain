from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = REPO_ROOT / "scripts"
PLUGIN_ROOT = REPO_ROOT / "plugin" / "goldsrc_model_toolchain"
sys.path.insert(0, str(SCRIPT_DIR))

import goldsrc_toolchain

if str(PLUGIN_ROOT) not in goldsrc_toolchain.__path__:
    goldsrc_toolchain.__path__.append(str(PLUGIN_ROOT))
sys.modules.setdefault("bpy", types.SimpleNamespace(app=types.SimpleNamespace(binary_path="blender")))

from goldsrc_toolchain.blender import pipeline


def _contract() -> dict:
    return {
        "version": 1,
        "target_profile": "half-life-cs",
        "model_name": "pipeline_fixture.mdl",
        "scale": 1.0,
        "bones": [{"name": "root", "parent": None}],
        "bodies": [{"name": "body", "source": "reference.smd", "object": "body_mesh"}],
        "bodygroups": [],
        "textures": [{"name": "base.bmp", "source": "base.bmp", "width": 64, "height": 64, "modes": []}],
        "skin_families": [],
        "sequences": [{
            "name": "idle", "source": "idle.smd", "action": "idle",
            "fps": 30, "frame": [0, 0], "loop": True,
            "events": [], "motion": [],
        }],
        "hitboxes": [],
        "attachments": [],
        "controllers": [],
        "bounds": {
            "bbox": {"min": [-1, -1, -1], "max": [1, 1, 1]},
            "cbox": {"min": [-1, -1, -1], "max": [1, 1, 1]},
        },
        "acceptance": {"required_phases": ["environment", "author"]},
    }


def _passing_stage(stage: str) -> dict:
    phase = stage.casefold()
    report = {"status": "pass", "phase": phase, "issues": [], "known_blockers": []}
    if stage == "PREFLIGHT":
        report["facts"] = {"meshes": [], "armatures": 1, "actions": ["idle"]}
    elif stage == "EXPORT":
        report.update({"references": [], "texture_selection": {"compiled": [], "omitted_unused_large_tiles": []}})
    elif stage == "COMPILE":
        report["inspection"] = {"bones": [], "sequences": [], "textures": [], "bodyparts": []}
    elif stage == "INSPECT":
        report.update({"inspections": {"sven": {}}, "animation_audits": {"sven": {}}})
    return report


def _roundtrip(pixel_hash: str = "same") -> dict:
    return {
        "status": "pass",
        "phase": "sourceio_roundtrip",
        "issues": [],
        "known_blockers": [],
        "facts": {
            "meshes": ["mesh"], "bones": 1, "textures": 1, "actions": ["idle"],
            "bodygroups": {"body": ["mesh"]}, "skin_family_count": 1,
            "action_matrix_audits": [],
            "weighted_vertex_audit": {"status": "pass", "checked_vertices": 3},
            "preview_pixel_hashes": [pixel_hash],
            "contact_sheet_pixel_hashes": ["sheet"],
            "preview_visibility": {"status": "pass"},
            "canonical_preview_pixel_hash": "canonical",
        },
        "previews": [], "contact_sheets": [],
    }


class RuntimePipelineTests(unittest.TestCase):
    def _contract_path(self, root: Path) -> Path:
        path = root / "contract.json"
        path.write_text(json.dumps(_contract()), encoding="utf-8")
        return path

    def test_pipeline_stops_at_first_failed_stage_and_keeps_full_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self._contract_path(root)
            calls = []

            def run(stage, _contract_path, _artifacts):
                calls.append(stage)
                if stage == "EXPORT":
                    return {
                        "status": "fail", "phase": "export",
                        "error": {"phase": "EXPORT", "code": "fixture.export", "message": "stop", "details": {}},
                        "issues": [{"severity": "error", "code": "fixture.export", "message": "stop"}],
                        "known_blockers": [],
                        "large_diagnostic": list(range(100)),
                    }
                return _passing_stage(stage)

            with mock.patch.object(pipeline, "execute_stage", side_effect=run):
                result = pipeline.execute_pipeline(
                    path, root, assurance="standard", visual_compare=False,
                    preserve_author_session=False, package_name="fixture",
                )
            self.assertEqual(calls, ["PREFLIGHT", "EXPORT"])
            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["failed_stage"], "EXPORT")
            persisted = json.loads((root / "reports" / "export.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["error"]["code"], "fixture.export")
            self.assertEqual(persisted["large_diagnostic"], list(range(100)))

    def test_strict_pipeline_repeats_only_isolated_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self._contract_path(root)
            stages = []
            isolated = []

            def run(stage, _contract_path, _artifacts):
                stages.append(stage)
                return _passing_stage(stage)

            def roundtrip(*_args, **_kwargs):
                isolated.append(_kwargs["evidence_dir"])
                return _roundtrip()

            with (
                mock.patch.object(pipeline, "execute_stage", side_effect=run),
                mock.patch.object(pipeline, "run_isolated_roundtrip", side_effect=roundtrip),
            ):
                result = pipeline.execute_pipeline(
                    path, root, assurance="strict", visual_compare=False,
                    preserve_author_session=True, package_name="fixture",
                )
            self.assertEqual(stages, ["PREFLIGHT", "EXPORT", "COMPILE", "INSPECT"])
            self.assertEqual(len(isolated), 2)
            self.assertEqual(result["status"], "pass")
            self.assertTrue((root / "reports" / "sourceio_roundtrip_repeat.json").is_file())

    def test_strict_pipeline_rejects_changed_decoded_pixels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self._contract_path(root)
            reports = iter((_roundtrip("first"), _roundtrip("second")))
            with (
                mock.patch.object(pipeline, "execute_stage", side_effect=lambda stage, *_: _passing_stage(stage)),
                mock.patch.object(pipeline, "run_isolated_roundtrip", side_effect=lambda *_a, **_k: next(reports)),
            ):
                result = pipeline.execute_pipeline(
                    path, root, assurance="strict", visual_compare=False,
                    preserve_author_session=True, package_name="fixture",
                )
            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["failed_stage"], "ROUNDTRIP_REPEAT")
            self.assertEqual(result["error"]["code"], "pipeline.roundtrip_nondeterministic")

    def test_delivery_is_atomic_and_does_not_touch_other_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mdl = root / "model.mdl"
            mdl.write_bytes(b"new-model")
            delivery = root / "delivery"
            delivery.mkdir()
            destination = delivery / mdl.name
            destination.write_bytes(b"existing-model")
            marker = delivery / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            with self.assertRaises(Exception) as caught:
                pipeline._deliver(mdl, delivery, replace_existing=False)
            self.assertEqual(getattr(caught.exception, "code", None), "delivery.exists")
            self.assertEqual(destination.read_bytes(), b"existing-model")
            result = pipeline._deliver(mdl, delivery, replace_existing=True)
            self.assertEqual(destination.read_bytes(), b"new-model")
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
            self.assertEqual(result["bytes"], len(b"new-model"))
            self.assertFalse(list(delivery.glob("*.tmp")))

    def test_pipeline_summary_distinguishes_author_and_tiled_triangles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self._contract_path(root)

            def run(stage, *_args):
                report = _passing_stage(stage)
                if stage == "EXPORT":
                    report.update({
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
                                "triangles": 1000,
                                "large_texture_tiling": [{"crossed_triangles": 108}],
                            },
                        }],
                        "texture_selection": {
                            "compiled": ["tile.bmp"],
                            "omitted_unused_large_tiles": [],
                        },
                    })
                return report

            with mock.patch.object(pipeline, "execute_stage", side_effect=run):
                result = pipeline.execute_pipeline(
                    path, root, assurance="standard", visual_compare=False,
                    preserve_author_session=False, package_name="fixture",
                )
            self.assertEqual(result["facts"]["author_triangles"], 720)
            self.assertEqual(result["facts"]["crossed_tile_triangles"], 108)
            self.assertEqual(result["facts"]["post_tile_triangles"], 1000)
            self.assertEqual(result["facts"]["triangles"], 1000)
            self.assertEqual(result["facts"]["material_mapping_audit"], "pass")

    def test_destructive_roundtrip_captures_author_before_visual_compare(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self._contract_path(root)
            calls = []
            author = {"path": str(root / "visual_compare" / "author_canonical.png")}

            def run(stage, *_args):
                calls.append(stage)
                return _roundtrip() if stage == "ROUNDTRIP" else _passing_stage(stage)

            def compare(*_args, **kwargs):
                self.assertIs(kwargs["author_preview"], author)
                return {"status": "pass", "checks": {}}

            visual_module = types.SimpleNamespace(
                create_static_author_preview=lambda *_args: author,
                create_static_visual_comparison=compare,
            )
            module_name = pipeline.__package__ + ".visual_compare"
            with (
                mock.patch.object(pipeline, "execute_stage", side_effect=run),
                mock.patch.dict(sys.modules, {module_name: visual_module}),
            ):
                result = pipeline.execute_pipeline(
                    path, root, assurance="standard", visual_compare=True,
                    preserve_author_session=False, package_name="fixture",
                )
            self.assertEqual(calls, ["PREFLIGHT", "EXPORT", "COMPILE", "INSPECT", "ROUNDTRIP"])
            self.assertEqual(result["status"], "pass")


if __name__ == "__main__":
    unittest.main()
