from __future__ import annotations

import ast
import importlib.util
import json
import hashlib
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
EXTENSION = REPO_ROOT / "extension" / "goldsrc_model_toolchain"


class ExtensionArchitectureTests(unittest.TestCase):
    def test_manifest_has_one_internal_plugin_and_external_mcp(self) -> None:
        manifest = json.loads((REPO_ROOT / "tool-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(set(manifest["bundles"]), {"goldsrc_model_toolchain"})
        self.assertEqual(manifest["bundles"]["goldsrc_model_toolchain"]["version"], "1.2.0")
        self.assertFalse(manifest["external_tools"]["blender_mcp"]["managed_by_extension"])
        self.assertEqual(manifest["external_tools"]["blender_mcp"]["ownership"], "external")

    def test_public_capabilities_and_bundled_compiler_are_declared(self) -> None:
        source = (EXTENSION / "api.py").read_text(encoding="utf-8")
        self.assertIn('"api_version": 1', source)
        self.assertIn('"distribution": "public_github_release"', source)
        self.assertIn('"repository": "https://github.com/XiangXtreme/goldsrc-model-toolchain"', source)
        self.assertIn('"multi_source_sequence_authoring": False', source)
        compiler = EXTENSION / "bin" / "windows-x64" / "studiomdl.exe"
        self.assertEqual(
            hashlib.sha256(compiler.read_bytes()).hexdigest(),
            "a96afa8b711be20e706fadcbe0f62d996812c4b3ccc3c4e3b617a3a12550276a",
        )

    def test_extension_has_no_ui_or_legacy_addon_imports(self) -> None:
        forbidden_paths = {"source1", "source2", "bsp", "dmx", "vta", "vtf", "vmt", "vpk"}
        forbidden_text = (
            "io_scene_valvesource", "import SourceIO", "from SourceIO.",
            "import blender_mcp", "from blender_mcp", "bpy.types.Panel", "bpy.types.Menu",
        )
        for path in EXTENSION.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            with self.subTest(path=path.relative_to(EXTENSION).as_posix()):
                self.assertFalse({part.casefold() for part in path.parts} & forbidden_paths)
                if path.suffix == ".py":
                    text = path.read_text(encoding="utf-8")
                    self.assertFalse([token for token in forbidden_text if token in text])

    def test_tool_repository_contains_no_skill_manifest(self) -> None:
        manifests = sorted(path.relative_to(REPO_ROOT).as_posix() for path in REPO_ROOT.rglob("SKILL.md"))
        self.assertEqual(manifests, [])

    def test_stage_operator_surface_is_fixed(self) -> None:
        source = (EXTENSION / "core" / "stages.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        assignments = {
            node.targets[0].id: ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "PUBLIC_STAGES"
        }
        self.assertEqual(assignments["PUBLIC_STAGES"], ("PREFLIGHT", "EXPORT", "COMPILE", "INSPECT", "ROUNDTRIP"))

    def test_roundtrip_reader_is_independent_and_strict(self) -> None:
        path = EXTENSION / "vendor" / "sourceio_goldsrc" / "reader.py"
        source = path.read_text(encoding="utf-8")
        imports = {
            alias.name
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module or ""
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom)
        )
        self.assertFalse(any("mdl_v10" in name or "goldsrc_toolchain" in name for name in imports))
        spec = importlib.util.spec_from_file_location("goldsrc_reader_unit", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
            with self.assertRaisesRegex(ValueError, "not a GoldSrc IDST model"):
                module.Mdl.from_bytes(b"NOPE")
            with self.assertRaisesRegex(ValueError, "read outside file"):
                module.Buffer(b"1234").read(5)
        finally:
            sys.modules.pop(spec.name, None)

    def test_host_stage_entries_do_not_reimplement_old_addons(self) -> None:
        for name in ("export_current_scene.py", "sourceio_roundtrip.py", "compile_model.py", "inspect_model.py"):
            text = (REPO_ROOT / "scripts" / name).read_text(encoding="utf-8")
            with self.subTest(name=name):
                self.assertNotIn("addon_utils", text)
                self.assertNotIn("io_scene_valvesource", text)
                self.assertNotIn("bpy.ops.sourceio", text)


if __name__ == "__main__":
    unittest.main()
