from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(SCRIPT_DIR))

from validate_workspace import validate


class WorkspaceValidationTests(unittest.TestCase):
    def test_current_workspace_binds_skill_and_plugin(self) -> None:
        report = validate()
        self.assertEqual(report["status"], "pass", report)
        self.assertEqual(report["skill"]["source"], "skill/build-goldsrc-models")
        self.assertEqual(report["plugin"]["source"], "plugin/goldsrc_model_toolchain")
        self.assertEqual(report["plugin"]["version"], "1.4.1")

    def test_rejects_mismatched_skill_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "workspace-manifest.json").write_text(
                json.dumps({"components": {"skill": {"source": "skill"}, "plugin": {"source": "plugin"}}}),
                encoding="utf-8",
            )
            (root / "skill" / "scripts").mkdir(parents=True)
            (root / "skill" / "SKILL.md").write_text("---\nname: test\n---\n", encoding="utf-8")
            (root / "skill" / "scripts" / "toolchain-release.json").write_text(
                json.dumps({"tag": "v0.0.0", "version": "0.0.0", "api_version": 1}),
                encoding="utf-8",
            )
            (root / "plugin").mkdir()
            (root / "plugin" / "blender_manifest.toml").write_text(
                'id = "goldsrc_model_toolchain"\nversion = "1.4.1"\nblender_version_min = "5.2.0"\n',
                encoding="utf-8",
            )
            (root / "tool-manifest.json").write_text(
                json.dumps({"bundles": {"goldsrc_model_toolchain": {"root": "plugin", "version": "1.4.1"}}}),
                encoding="utf-8",
            )
            report = validate(root)
            self.assertEqual(report["status"], "fail")
            self.assertTrue(any("Skill release tag mismatch" in item for item in report["errors"]))


if __name__ == "__main__":
    unittest.main()
