from __future__ import annotations

import importlib.util
import re
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "install_toolchain.py"
SPEC = importlib.util.spec_from_file_location("goldsrc_skill_installer", SCRIPT)
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)


class InstallerTests(unittest.TestCase):
    def test_release_manifest_is_pinned_to_public_v133(self) -> None:
        release = INSTALLER.load_release()
        self.assertEqual(release["repository"], "https://github.com/XiangXtreme/goldsrc-model-toolchain")
        self.assertEqual(release["tag"], "v1.3.3")
        self.assertEqual(release["asset"], "goldsrc_model_toolchain-1.3.3-windows-x64.zip")
        self.assertEqual(release["sha256"], "82d600a309f2e53e97085efe97f43e9be1ca76ff0df51d308fac5cae7b21415a")
        self.assertEqual(release["api_version"], 1)
        self.assertRegex(release["sha256"], r"^[0-9a-f]{64}$")

    def test_version_routing_never_downgrades_newer_api_one(self) -> None:
        release = INSTALLER.load_release()
        self.assertEqual(INSTALLER.version_compatibility(None, release), "missing")
        self.assertEqual(
            INSTALLER.version_compatibility({"id": "goldsrc_model_toolchain", "version": "1.3.3", "api_version": 1}, release),
            "validated",
        )
        self.assertEqual(
            INSTALLER.version_compatibility({"id": "goldsrc_model_toolchain", "version": "1.4.0", "api_version": 1}, release),
            "compatible_unregressed_version",
        )
        self.assertEqual(
            INSTALLER.version_compatibility({"id": "goldsrc_model_toolchain", "version": "1.3.1", "api_version": 1}, release),
            "upgrade_required",
        )

    def test_archive_hash_is_enforced(self) -> None:
        release = INSTALLER.load_release()
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "fixture.zip"
            archive.write_bytes(b"not the release")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                INSTALLER._validate_archive(archive, release)

    def test_skill_has_no_bundled_toolchain_or_pipeline(self) -> None:
        self.assertFalse((ROOT / "tools").exists())
        self.assertFalse((ROOT / "scripts" / "run_model_pipeline.py").exists())
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in (
            "mcp_servers",
            "config.toml",
            "ahujasid",
            "blender-mcp",
            "addon.py",
        ):
            self.assertNotIn(forbidden, source.casefold())
        self.assertIn('"blender_mcp_modified": False', source)

    def test_skill_structure_and_markdown_links_are_clean(self) -> None:
        self.assertEqual(list(ROOT.rglob("SKILL.md")), [ROOT / "SKILL.md"])
        for forbidden in ("tools", "work", "artifacts", "outputs", "dist"):
            self.assertFalse((ROOT / forbidden).exists(), forbidden)
        self.assertFalse(list(ROOT.rglob("__pycache__")))
        link_pattern = re.compile(r"\[[^\]\r\n]+\]\(([^)\r\n]+)\)")
        for document in ROOT.rglob("*.md"):
            for target in link_pattern.findall(document.read_text(encoding="utf-8")):
                target = target.strip().strip("<>")
                if not target or target.startswith("#") or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target):
                    continue
                path = (document.parent / target.split("#", 1)[0]).resolve()
                with self.subTest(document=document.name, target=target):
                    self.assertTrue(path.is_file(), path)
                    self.assertTrue(path.is_relative_to(ROOT), path)

    def test_creation_guidance_precedes_tooling(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertLess(skill.index("## Understand The Asset"), skill.index("## Choose Assurance"))
        self.assertLess(skill.index("## Choose Assurance"), skill.index("## Use The External Toolchain"))
        self.assertIn("references/pitfalls.md", skill)
        self.assertIn("references/workflow-advanced-fx.md", skill)
        self.assertNotIn("references/production-workflow.md", skill)

    def test_advanced_workflow_and_new_pitfalls_are_routed(self) -> None:
        advanced = (ROOT / "references" / "workflow-advanced-fx.md").read_text(encoding="utf-8")
        for topic in ("Flame, Smoke, And Embers", "Fake Specular", "Intermediate Bones", "Image-Plane Detail", "Skin Families"):
            self.assertIn(topic, advanced)
        pitfalls = (ROOT / "references" / "pitfalls.md").read_text(encoding="utf-8")
        for identifier in ("TEX-07", "QC-01", "QC-02", "QC-03", "QC-04", "ANIM-06", "PLAYER-01", "NPC-01", "READBACK-02"):
            self.assertEqual(pitfalls.count(f"### {identifier} "), 1)
        self.assertIn("`-3000`", pitfalls)
        self.assertNotIn("`-3` = OS/2", pitfalls)
        animation = (ROOT / "references" / "workflow-animation-characters.md").read_text(encoding="utf-8")
        self.assertIn("duplicate seam endpoint", animation)
        self.assertIn("SMD declaration order", animation)


if __name__ == "__main__":
    unittest.main()
