from __future__ import annotations

import ast
import importlib.util
import json
import hashlib
import struct
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
EXTENSION = REPO_ROOT / "plugin" / "goldsrc_model_toolchain"


class ExtensionArchitectureTests(unittest.TestCase):
    def test_manifest_has_one_internal_plugin_and_external_mcp(self) -> None:
        manifest = json.loads((REPO_ROOT / "tool-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(set(manifest["bundles"]), {"goldsrc_model_toolchain"})
        bundle = manifest["bundles"]["goldsrc_model_toolchain"]
        self.assertEqual(manifest["schema_version"], 3)
        self.assertEqual(manifest["distribution_channel"], "public_github_release")
        self.assertEqual(bundle["version_source"], "blender_manifest.toml")
        self.assertFalse({"version", "files", "bytes", "sha256_tree"} & bundle.keys())
        self.assertFalse({"tag", "asset"} & manifest["public_release"].keys())
        self.assertFalse(manifest["external_tools"]["blender_mcp"]["managed_by_extension"])
        self.assertEqual(manifest["external_tools"]["blender_mcp"]["ownership"], "external")

    def test_public_capabilities_and_bundled_compiler_are_declared(self) -> None:
        source = (EXTENSION / "api.py").read_text(encoding="utf-8")
        manifest = tomllib.loads((EXTENSION / "blender_manifest.toml").read_text(encoding="utf-8"))
        identity_path = EXTENSION / "core" / "release_identity.py"
        spec = importlib.util.spec_from_file_location("goldsrc_release_identity_unit", identity_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        identity = module.load_release_identity(EXTENSION / "blender_manifest.toml")
        self.assertEqual(identity["version"], manifest["version"])
        self.assertEqual(identity["release"], f"v{manifest['version']}")
        with tempfile.TemporaryDirectory() as temporary:
            development = Path(temporary) / "blender_manifest.toml"
            development.write_text(
                'id = "goldsrc_model_toolchain"\nversion = "9.0.0-dev"\n', encoding="utf-8",
            )
            development_identity = module.load_release_identity(development)
        self.assertEqual(development_identity["distribution"], "development_build")
        self.assertIsNone(development_identity["release"])
        self.assertIn('"version": release["version"]', source)
        self.assertIn('"api_version": 1', source)
        self.assertIn('"distribution": release["distribution"]', source)
        self.assertIn('"release": release["release"]', source)
        self.assertIn('"repository": "https://github.com/XiangXtreme/goldsrc-model-toolchain"', source)
        self.assertIn('"multi_source_sequence_authoring": False', source)
        self.assertIn('"smd_animation_binding": True', source)
        self.assertIn('"mdl_decompile": True', source)
        self.assertIn('"roundtrip_matrix_audit": True', source)
        self.assertIn('"loop_endpoint_validation": True', source)
        self.assertIn('"bounds_aware_roundtrip_camera": True', source)
        self.assertIn('"blank_preview_rejection": True', source)
        self.assertIn('"roundtrip_decoded_pixel_hash": True', source)
        self.assertIn('"preflight_material_texture_token": True', source)
        self.assertIn('"export_time_triangulation": True', source)
        self.assertIn('"evaluated_uv_material_reports": True', source)
        self.assertIn('"texture_bake_uv_guard": True', source)
        self.assertIn('"failed_requirement_evidence": True', source)
        self.assertIn('"stage_report_persistence": True', source)
        self.assertIn('"summary_stage_results": True', source)
        self.assertIn('"isolated_roundtrip": True', source)
        self.assertIn('"static_selection_analysis": True', source)
        self.assertIn('"non_destructive_static_prepare": True', source)
        self.assertIn('"static_contract_from_scene": True', source)
        self.assertIn('"selected_static_export": True', source)
        self.assertIn('"strict_static_pipeline": True', source)
        self.assertIn('"unified_static_visual_compare": True', source)
        self.assertIn('"evaluated_material_mapping_audit": True', source)
        self.assertIn('"large_texture_tiling": {', source)
        self.assertIn('"sparse_compiled_tiles": True', source)
        self.assertIn('"smd_budget_split": {', source)
        self.assertIn('"high_quality_texture_quantization": "Pillow MEDIANCUT"', source)
        self.assertIn('"texture_fidelity_report": True', source)
        self.assertIn('"labeled_visual_contact_sheets": True', source)
        self.assertIn("def create_visual_contact_sheet", source)
        self.assertIn("def analyze_selected_static", source)
        self.assertIn("def prepare_static_export", source)
        self.assertIn("def export_selected_static", source)
        self.assertIn("def create_static_contract_from_scene", source)
        self.assertIn("def execute_pipeline", source)
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

    def test_workspace_contains_one_skill_manifest(self) -> None:
        manifests = sorted(path.relative_to(REPO_ROOT).as_posix() for path in REPO_ROOT.rglob("SKILL.md"))
        self.assertEqual(manifests, ["skill/build-goldsrc-models/SKILL.md"])

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

    def test_static_export_fixture_covers_realistic_unprepared_asset(self) -> None:
        source = (REPO_ROOT / "scripts" / "static_export_fixture.py").read_text(encoding="utf-8")
        for token in (
            '"2048"', '"NODES"', 'ShaderNodeMixShader', 'ShaderNodeBsdfPrincipled',
            'uv_strategy="smart_project"', 'bake_mode="unlit_color"',
            'api.export_selected_static(',
            'GOLDSRC_STATIC_ASSURANCE", "strict"',
            'GOLDSRC_STATIC_PRESERVE_SESSION", "1"',
            'GOLDSRC_STATIC_ALPHA_FIXTURE',
            'GeometryNodeSetMaterial', 'AutoTerrain_base',
            'static_material_audit',
            'GOLDSRC_STATIC_EXPECT_AUDIT_FAILURE',
        ):
            self.assertIn(token, source)

    def test_static_prepare_is_conservative_about_material_semantics(self) -> None:
        source = (EXTENSION / "blender" / "static_export.py").read_text(encoding="utf-8")
        for token in (
            'node.type == "GROUP"', 'unsupported_alpha_bake',
            'transparent_alpha_requires_artistic_resolution',
            'def _bake_material_alpha', 'type="EMIT"',
        ):
            self.assertIn(token, source)
        self.assertNotIn("BranchedCave", source)

    def test_workspace_scripts_are_documented_outside_the_extension_payload(self) -> None:
        scripts_readme = (REPO_ROOT / "scripts" / "README.md").read_text(encoding="utf-8")
        builder = (REPO_ROOT / "scripts" / "build_extension.py").read_text(encoding="utf-8")
        self.assertIn("not Blender Extension source", scripts_readme)
        self.assertIn('SOURCE = REPO_ROOT / "plugin" / "goldsrc_model_toolchain"', builder)
        self.assertFalse((EXTENSION / "scripts").exists())

    def test_isolated_roundtrip_supports_installed_and_source_packages(self) -> None:
        launcher = (EXTENSION / "blender" / "isolated_roundtrip.py").read_text(encoding="utf-8")
        worker = (EXTENSION / "blender" / "roundtrip_worker.py").read_text(encoding="utf-8")
        self.assertIn('"--addons", package_name', launcher)
        self.assertIn('"--module-root"', launcher)
        self.assertIn('sys.path.insert(0, module_root)', worker)

    def test_unified_visual_compare_reproduces_compiler_sampling(self) -> None:
        source = (EXTENSION / "blender" / "visual_compare.py").read_text(encoding="utf-8")
        for token in (
            'compare.operation = "GREATER_THAN"',
            'math.floor(u * width + 0.5) / width',
            'math.floor((1.0 - v) * height + 0.5) / height',
            'quantize_smd=apply_root_axis',
            'mesh.materials[material_index] = canonical',
        ):
            self.assertIn(token, source)
        self.assertNotIn('mesh.materials.clear()', source)

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
            texture_blob = (
                b"plain_mask.bmp\0".ljust(64, b"\0")
                + struct.pack("<4I", 0x0040, 1, 1, 80)
                + b"\xff"
                + bytes(256 * 3)
            )
            texture = module.Texture.read(module.Buffer(texture_blob))
            self.assertEqual(float(texture.pixels[0, 0, 3]), 0.0)
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
