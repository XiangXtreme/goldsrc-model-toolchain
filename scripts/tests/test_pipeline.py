from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldsrc_toolchain.textures import convert_to_indexed_bmp


SCRIPT_DIR = Path(__file__).resolve().parents[1]


class PipelineTests(unittest.TestCase):
    @staticmethod
    def _contract(model_name: str) -> dict:
        return {
            "version": 1,
            "target_profile": "half-life-cs",
            "model_name": model_name,
            "scale": 1.0,
            "bones": [{"name": "root", "parent": None}],
            "bodies": [{"name": "body", "source": "reference.smd", "object": "body_mesh"}],
            "bodygroups": [],
            "textures": [{"name": "base.bmp", "source": "base.bmp", "width": 64, "height": 64, "modes": []}],
            "skin_families": [],
            "sequences": [{"name": "idle", "source": "idle.smd", "action": "idle", "fps": 30, "frame": [0, 0], "loop": True, "events": [], "motion": []}],
            "hitboxes": [{"group": 0, "bone": "root", "min": [-1, -1, -1], "max": [1, 1, 1]}],
            "attachments": [],
            "controllers": [],
            "bounds": {
                "bbox": {"min": [-1, -1, -1], "max": [1, 1, 1]},
                "cbox": {"min": [-2, -2, -2], "max": [2, 2, 2]},
            },
            "acceptance": {"required_phases": ["environment", "author"]},
        }

    @classmethod
    def _v2_contract(cls, model_name: str) -> dict:
        value = cls._contract(model_name)
        value["version"] = 2
        value["acceptance"] = {"required_phases": ["author"]}
        value["intent"] = {
            "request": "Build a model whose intact state is visible.",
            "requirements": [
                {"id": "intact-visible", "source": "intact state is visible", "evidence_phases": ["author"]},
            ],
            "assumptions": [],
        }
        return value

    def test_known_blockers_are_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifacts = root / "artifacts"
            stage = root / "stage.py"
            stage.write_text(
                "from pathlib import Path\nimport json, os\n"
                "Path(os.environ['OUT']).write_text(json.dumps({'status':'pass_with_known_blockers','known_blockers':[{'code':'same','message':'known'}]}))\n",
                encoding="utf-8",
            )
            stages = []
            for index in range(2):
                stages.append({
                    "name": f"stage_{index}", "runner": "python", "script": str(stage),
                    "environment": {"OUT": f"{{artifacts}}/result_{index}.json"},
                    "outputs": [f"result_{index}.json"], "result_json": f"result_{index}.json",
                })
            spec_path = root / "pipeline.json"
            spec_path.write_text(json.dumps({"artifacts": str(artifacts), "stages": stages}), encoding="utf-8")
            completed = subprocess.run([sys.executable, str(SCRIPT_DIR / "run_model_pipeline.py"), str(spec_path)], capture_output=True, text=True, errors="replace", timeout=30)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            report = json.loads((artifacts / "model_pipeline_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "pass_with_known_blockers")
            self.assertEqual(len(report["known_blockers"]), 1)

    def test_cache_hit_and_input_invalidation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifacts = root / "artifacts"
            source = root / "input.txt"
            source.write_text("one", encoding="utf-8")
            stage = root / "stage.py"
            stage.write_text(
                "from pathlib import Path\nimport os\n"
                "Path(os.environ['OUT']).write_text(Path(os.environ['IN']).read_text())\n",
                encoding="utf-8",
            )
            spec = {
                "artifacts": str(artifacts),
                "environment": {"IN": str(source), "OUT": "{artifacts}/result.txt"},
                "stages": [{"name": "copy", "runner": "python", "script": str(stage), "inputs": [str(source)], "outputs": ["result.txt"]}],
            }
            spec_path = root / "pipeline.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")

            def run() -> dict:
                completed = subprocess.run([sys.executable, str(SCRIPT_DIR / "run_model_pipeline.py"), str(spec_path)], capture_output=True, text=True, errors="replace", timeout=30)
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                return json.loads((artifacts / "model_pipeline_report.json").read_text(encoding="utf-8"))

            self.assertEqual(run()["stages"]["copy"]["status"], "executed")
            self.assertEqual(run()["stages"]["copy"]["status"], "cached")
            (artifacts / "result.txt").write_text("tampered", encoding="utf-8")
            self.assertEqual(run()["stages"]["copy"]["status"], "executed")
            self.assertEqual((artifacts / "result.txt").read_text(), "one")
            source.write_text("two", encoding="utf-8")
            self.assertEqual(run()["stages"]["copy"]["status"], "executed")
            self.assertEqual((artifacts / "result.txt").read_text(), "two")

    def test_reuse_report_stage_is_explicitly_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifacts = root / "artifacts"
            source = artifacts / "author.json"
            source.parent.mkdir(parents=True)
            source.write_text(json.dumps({"status": "pass", "facts": {"checkpoint": "valid"}}), encoding="utf-8")
            spec_path = root / "pipeline.json"
            spec_path.write_text(json.dumps({
                "artifacts": str(artifacts),
                "stages": [{
                    "name": "author",
                    "phase": "author",
                    "runner": "reuse_report",
                    "source_report": str(source),
                    "outputs": ["author_reused.json"],
                    "result_json": "author_reused.json",
                }],
            }), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(SCRIPT_DIR / "run_model_pipeline.py"), str(spec_path)],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            report = json.loads((artifacts / "model_pipeline_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["stages"]["author"]["status"], "reused")
            self.assertEqual(json.loads((artifacts / "author_reused.json").read_text(encoding="utf-8")), json.loads(source.read_text(encoding="utf-8")))

    def test_contract_change_keeps_environment_cached_and_reruns_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifacts = root / "artifacts"
            contract_path = root / "model_contract.json"
            contract_path.write_text(json.dumps(self._contract("first.mdl")), encoding="utf-8")
            stage = root / "stage.py"
            stage.write_text(
                "from pathlib import Path\nimport json, os\n"
                "value = os.environ.get('VALUE', 'environment-ready')\n"
                "if 'CONTRACT' in os.environ:\n"
                "    value = json.loads(Path(os.environ['CONTRACT']).read_text())['model_name']\n"
                "Path(os.environ['OUT']).write_text(value)\n",
                encoding="utf-8",
            )
            spec = {
                "artifacts": str(artifacts),
                "contract": str(contract_path),
                "stages": [
                    {
                        "name": "environment",
                        "phase": "environment",
                        "runner": "python",
                        "script": str(stage),
                        "environment": {"OUT": "{artifacts}/environment.txt"},
                        "outputs": ["environment.txt"],
                    },
                    {
                        "name": "author",
                        "phase": "author",
                        "runner": "python",
                        "script": str(stage),
                        "environment": {"CONTRACT": str(contract_path), "OUT": "{artifacts}/author.txt"},
                        "outputs": ["author.txt"],
                    },
                ],
            }
            spec_path = root / "pipeline.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")

            def run() -> dict:
                completed = subprocess.run(
                    [sys.executable, str(SCRIPT_DIR / "run_model_pipeline.py"), str(spec_path)],
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=30,
                )
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                return json.loads((artifacts / "model_pipeline_report.json").read_text(encoding="utf-8"))

            first = run()
            self.assertEqual(first["stages"]["environment"]["status"], "executed")
            self.assertEqual(first["stages"]["author"]["status"], "executed")
            contract_path.write_text(json.dumps(self._contract("second.mdl")), encoding="utf-8")
            second = run()
            self.assertEqual(second["stages"]["environment"]["status"], "cached")
            self.assertEqual(second["stages"]["author"]["status"], "executed")
            self.assertEqual((artifacts / "author.txt").read_text(encoding="utf-8"), "second.mdl")

    def test_failed_result_does_not_emit_success_claims(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifacts = root / "artifacts"
            stage = root / "stage.py"
            stage.write_text(
                "from pathlib import Path\nimport json, os\n"
                "Path(os.environ['OUT']).write_text(json.dumps({'status':'fail'}))\n",
                encoding="utf-8",
            )
            spec = {
                "artifacts": str(artifacts),
                "environment": {"OUT": "{artifacts}/result.json"},
                "stages": [{"name": "bad", "runner": "python", "phase": "compile_sven", "script": str(stage), "outputs": ["result.json"], "result_json": "result.json"}],
            }
            spec_path = root / "pipeline.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            completed = subprocess.run([sys.executable, str(SCRIPT_DIR / "run_model_pipeline.py"), str(spec_path)], capture_output=True, text=True, errors="replace", timeout=30)
            self.assertNotEqual(completed.returncode, 0)
            report = json.loads((artifacts / "model_pipeline_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "fail")
            self.assertFalse(any(report["claims"].values()))

    def test_export_postcondition_rejects_invalid_artifacts_before_compile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifacts = root / "artifacts"
            contract = self._contract("postcondition.mdl")
            contract["acceptance"] = {"required_phases": ["export"]}
            contract_path = root / "model_contract.json"
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            source = root / "source.png"
            texture = root / "base.bmp"
            Image.new("RGBA", (64, 64), (80, 120, 160, 255)).save(source)
            convert_to_indexed_bmp(source, texture, width=64, height=64)
            stage = root / "export.py"
            stage.write_text(
                "from pathlib import Path\nimport json, os, shutil\n"
                "root = Path(os.environ['ARTIFACTS'])\nroot.mkdir(parents=True, exist_ok=True)\n"
                "reference = '''version 1\nnodes\n0 \"root\" -1\nend\nskeleton\ntime 0\n0 0 0 0 0 0 0\nend\ntriangles\nundeclared.bmp\n0 0 0 0 0 0 1 0 0\n0 1 0 0 0 0 1 1 0\n0 0 1 0 0 0 1 0 1\nend\n'''\n"
                "idle = '''version 1\nnodes\n0 \"root\" -1\nend\nskeleton\ntime 0\n0 0 0 0 0 0 0\nend\n'''\n"
                "(root / 'reference.smd').write_text(reference)\n"
                "(root / 'idle.smd').write_text(idle)\n"
                "shutil.copyfile(os.environ['TEXTURE'], root / 'base.bmp')\n"
                "(root / 'export.json').write_text(json.dumps({'status': 'pass'}))\n",
                encoding="utf-8",
            )
            compile_stage = root / "compile.py"
            compile_stage.write_text(
                "from pathlib import Path\nimport os\nPath(os.environ['MARKER']).write_text('compiled')\n",
                encoding="utf-8",
            )
            spec = {
                "artifacts": str(artifacts),
                "contract": str(contract_path),
                "stages": [
                    {
                        "name": "export",
                        "phase": "export",
                        "runner": "python",
                        "script": str(stage),
                        "environment": {"ARTIFACTS": "{artifacts}", "TEXTURE": str(texture)},
                        "outputs": ["reference.smd", "idle.smd", "base.bmp", "export.json"],
                        "result_json": "export.json",
                    },
                    {
                        "name": "compile",
                        "phase": "compile_sven",
                        "runner": "python",
                        "script": str(compile_stage),
                        "environment": {"MARKER": "{artifacts}/compiled.txt"},
                        "outputs": ["compiled.txt"],
                    },
                ],
            }
            spec_path = root / "pipeline.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(SCRIPT_DIR / "run_model_pipeline.py"), str(spec_path)],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=30,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse((artifacts / "compiled.txt").exists())
            report = json.loads((artifacts / "model_pipeline_report.json").read_text(encoding="utf-8"))
            self.assertIn("SMD material is absent from textures", report["error"])

    def test_version_two_requires_stage_evidence_for_every_requirement(self) -> None:
        cases = [
            (
                "passing",
                [{
                    "id": "intact-visible",
                    "status": "pass",
                    "summary": "The intact model is present in the author checkpoint.",
                    "evidence": {"object_count": 1},
                }],
                0,
                "pass",
            ),
            ("missing", [], 1, "fail"),
        ]
        for label, requirement_evidence, expected_code, expected_status in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                artifacts = root / "artifacts"
                contract_path = root / "model_contract.json"
                contract_path.write_text(json.dumps(self._v2_contract("intent.mdl")), encoding="utf-8")
                stage = root / "stage.py"
                stage.write_text(
                    "from pathlib import Path\nimport json, os\n"
                    f"requirement_evidence = {requirement_evidence!r}\n"
                    "Path(os.environ['OUT']).write_text(json.dumps({'status':'pass','requirement_evidence':requirement_evidence}))\n",
                    encoding="utf-8",
                )
                spec = {
                    "artifacts": str(artifacts),
                    "contract": str(contract_path),
                    "environment": {
                        "OUT": "{artifacts}/author.json",
                    },
                    "stages": [{
                        "name": "author",
                        "phase": "author",
                        "runner": "python",
                        "script": str(stage),
                        "outputs": ["author.json"],
                        "result_json": "author.json",
                    }],
                }
                spec_path = root / "pipeline.json"
                spec_path.write_text(json.dumps(spec), encoding="utf-8")
                completed = subprocess.run(
                    [sys.executable, str(SCRIPT_DIR / "run_model_pipeline.py"), str(spec_path)],
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=30,
                )
                self.assertEqual(completed.returncode, expected_code, completed.stdout + completed.stderr)
                report = json.loads((artifacts / "model_pipeline_report.json").read_text(encoding="utf-8"))
                self.assertEqual(report["status"], expected_status)
                self.assertEqual(report["requirements"]["intact-visible"]["status"], expected_status)


if __name__ == "__main__":
    unittest.main()
