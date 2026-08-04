from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from prepare_release import prepare_release
from release_metadata import (
    PLUGIN_MANIFEST,
    SKILL_RELEASE_PIN,
    load_skill_release,
    plugin_version,
    release_coordinates,
    set_plugin_version,
)


class ReleaseMetadataTests(unittest.TestCase):
    def _workspace(self, root: Path) -> None:
        manifest = root / PLUGIN_MANIFEST
        manifest.parent.mkdir(parents=True)
        manifest.write_bytes(
            b'schema_version = "1.0.0"\r\nid = "goldsrc_model_toolchain"\r\n'
            b'version = "1.2.3"\r\nblender_version_min = "5.2.0"\r\n'
        )
        pin = root / SKILL_RELEASE_PIN
        pin.parent.mkdir(parents=True)
        pin.write_text(json.dumps({
            "schema_version": 2,
            "repository": "https://github.com/XiangXtreme/goldsrc-model-toolchain",
            "version": "1.2.3",
            "sha256": "a" * 64,
            "extension_id": "goldsrc_model_toolchain",
            "api_version": 1,
            "blender": "5.2.x",
            "platform": "windows-x64",
        }), encoding="utf-8")
        (root / "unchanged.txt").write_text("stable\n", encoding="utf-8")

    def test_release_coordinates_are_derived_from_one_version(self) -> None:
        release = release_coordinates("2.3.4")
        self.assertEqual(release["tag"], "v2.3.4")
        self.assertEqual(release["asset"], "goldsrc_model_toolchain-2.3.4-windows-x64.zip")
        self.assertTrue(release["download_url"].endswith("/v2.3.4/goldsrc_model_toolchain-2.3.4-windows-x64.zip"))

    def test_manifest_version_update_preserves_line_endings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._workspace(root)
            set_plugin_version("1.2.4", root)
            data = (root / PLUGIN_MANIFEST).read_bytes()
            self.assertIn(b'version = "1.2.4"\r\n', data)
            self.assertNotIn(b'\n', data.replace(b"\r\n", b""))

    def test_prepare_release_changes_only_manifest_and_skill_pin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "workspace"
            output = base / "release"
            self._workspace(root)
            before = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }

            def fake_build(archive: Path, *, blender=None) -> dict:
                self.assertEqual(plugin_version(root), "1.2.4")
                archive.parent.mkdir(parents=True, exist_ok=True)
                archive.write_bytes(b"release archive")
                return {
                    "status": "pass",
                    "version": "1.2.4",
                    "archive": str(archive),
                    "bytes": archive.stat().st_size,
                    "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                }

            result = prepare_release(
                "1.2.4", output, root=root, build_func=fake_build,
                validate_func=lambda _root: {"status": "pass"},
            )
            after = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }
            changed = {path for path in after if before.get(path) != after[path]}
            self.assertEqual(changed, {PLUGIN_MANIFEST, SKILL_RELEASE_PIN})
            self.assertEqual(
                result["source_files"],
                [PLUGIN_MANIFEST.as_posix(), SKILL_RELEASE_PIN.as_posix()],
            )
            release = load_skill_release(root)
            self.assertEqual(release["version"], "1.2.4")
            self.assertEqual(release["asset"], Path(result["archive"]).name)
            self.assertTrue(Path(result["checksum"]).is_file())

    def test_prepare_release_rolls_back_sources_and_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "workspace"
            output = base / "release"
            self._workspace(root)
            originals = {
                path: (root / path).read_bytes()
                for path in (PLUGIN_MANIFEST, SKILL_RELEASE_PIN)
            }

            def failed_build(archive: Path, *, blender=None) -> dict:
                archive.parent.mkdir(parents=True, exist_ok=True)
                archive.write_bytes(b"partial")
                raise RuntimeError("fixture build failed")

            with self.assertRaisesRegex(RuntimeError, "fixture build failed"):
                prepare_release(
                    "1.2.4", output, root=root, build_func=failed_build,
                    validate_func=lambda _root: {"status": "pass"},
                )
            for path, data in originals.items():
                self.assertEqual((root / path).read_bytes(), data)
            self.assertFalse(list(output.glob("*")))


if __name__ == "__main__":
    unittest.main()
