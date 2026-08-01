from __future__ import annotations

import copy
import json
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from goldsrc_toolchain.mdl_v10 import _decode_animation_value, inspect_mdl, validate_mdl_contract
from goldsrc_toolchain.model_contract import ContractError, effective_texture_modes, render_qc, validate_contract
from goldsrc_toolchain.paths import ensure_outside_skill_tree, resolve_artifact_root
from goldsrc_toolchain.smd import SmdError, compiled_model_vertex_count, parse_smd, validate_smd
from goldsrc_toolchain.textures import (
    TextureError,
    _convert_with_blender_image,
    _write_indexed_bmp_from_rgba,
    convert_to_indexed_bmp,
    validate_indexed_bmp,
)
from bootstrap_environment import (
    MCP_PROTOCOL_BASELINE,
    MCP_SERVER_BASELINE,
    _canonical_bytes,
    _codex_fact,
    verify_bundles,
    _repository_layout_fact,
)
from audit_repository import audit
from validate_model import _preview


REPO_ROOT = SCRIPT_DIR.parent


def base_contract() -> dict:
    return {
        "version": 2,
        "intent": {
            "request": "Build one textured idle model.",
            "requirements": [
                {"id": "idle-model", "source": "one textured idle model", "evidence_phases": ["author"]},
            ],
            "assumptions": [],
        },
        "target_profile": "half-life-cs",
        "model_name": "unit_model.mdl",
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
    }


class ArtifactIsolationTests(unittest.TestCase):
    def test_rejects_artifacts_and_writes_inside_skill_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "SKILL.md").write_text("---\nname: test\n---\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "outside Skill directory"):
                resolve_artifact_root(root / "work" / "scene")
            with self.assertRaisesRegex(ValueError, "outside Skill directory"):
                ensure_outside_skill_tree(root / "release.zip", label="Archive")

    def test_accepts_external_temporary_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(resolve_artifact_root(temporary), Path(temporary).resolve())

    def test_package_collection_rejects_runtime_directories_and_nested_skills(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "SKILL.md").write_text("---\nname: test\n---\n", encoding="utf-8")
            nested = root / "work" / "copy"
            nested.mkdir(parents=True)
            (nested / "SKILL.md").write_text("---\nname: copy\n---\n", encoding="utf-8")
            local_settings = root / ".claude" / "settings.local.json"
            local_settings.parent.mkdir()
            local_settings.write_text("{}\n", encoding="utf-8")
            report = audit(root)
            self.assertTrue(any("runtime directory" in item for item in report["errors"]))
            self.assertTrue(any("Skill manifest" in item for item in report["errors"]))

    def test_release_archive_audit_uses_and_removes_system_temp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "fixture.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("fixture/file.txt", "ok")
            completed = subprocess.run(
                [sys.executable, str(SCRIPT_DIR / "audit_release_archives.py"), str(archive)],
                capture_output=True, text=True, errors="replace", timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            report = json.loads(completed.stdout)
            self.assertEqual(report["status"], "pass")
            self.assertTrue(report["temporary_directory_removed"])

    def test_release_archive_audit_rejects_local_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "polluted.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("skill/.claude/settings.local.json", "{}")
            completed = subprocess.run(
                [sys.executable, str(SCRIPT_DIR / "audit_release_archives.py"), str(archive)],
                capture_output=True, text=True, errors="replace", timeout=30,
            )
            self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
            report = json.loads(completed.stdout)
            self.assertIn("forbidden local/runtime", report["archives"][0]["errors"][0])

    def test_current_repository_layout_is_discovery_safe(self) -> None:
        self.assertEqual(
            _repository_layout_fact(),
            {"valid": True, "skill_manifests": [], "runtime_directories": []},
        )


class BundleIntegrityTests(unittest.TestCase):
    def test_canonicalizes_text_line_endings_without_rewriting_binary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            text_path = root / "source.py"
            binary_path = root / "tool.exe"
            text_path.write_bytes(b"one\r\ntwo\r\n")
            binary_path.write_bytes(b"MZ\x00one\r\ntwo")
            self.assertEqual(_canonical_bytes(text_path), b"one\ntwo\n")
            self.assertEqual(_canonical_bytes(binary_path), binary_path.read_bytes())

    def test_mcp_config_is_read_only_and_recognizes_verified_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.toml"
            original = (
                '[mcp_servers.blender-mcp]\ncommand = "uvx"\n'
                f'args = ["--with", "{MCP_PROTOCOL_BASELINE}", "{MCP_SERVER_BASELINE}"]\n\n'
                '[mcp_servers.keep-me]\ncommand = "other"\n'
            )
            config.write_text(original, encoding="utf-8")
            with mock.patch.dict("os.environ", {"GOLDSRC_CODEX_CONFIG": str(config)}):
                fact = _codex_fact()
            self.assertTrue(fact["configured"])
            self.assertTrue(fact["verified_baseline"])
            self.assertEqual(config.read_text(encoding="utf-8"), original)

    def test_manifest_declares_one_valid_extension_bundle(self) -> None:
        report = verify_bundles()
        self.assertEqual(report["status"], "pass", report)
        self.assertEqual(set(report["bundles"]), {"goldsrc_model_toolchain"})


REFERENCE_SMD = """version 1
nodes
0 \"root\" -1
end
skeleton
time 0
0 0 0 0 0 0 0
end
triangles
base.bmp
0 -1 -1 0 0 0 1 0 0
0 1 -1 0 0 0 1 1 0
0 0 1 0 0 0 1 0.5 1
end
"""

IDLE_SMD = """version 1
nodes
0 \"root\" -1
end
skeleton
time 0
0 0 0 0 0 0 0
end
"""


class ContractTests(unittest.TestCase):
    def assert_contract_error(self, mutate, needle: str) -> None:
        value = base_contract()
        mutate(value)
        with self.assertRaises(ContractError) as caught:
            validate_contract(value)
        self.assertIn(needle, str(caught.exception))

    def test_normalizes_outputs_and_renders_qc(self) -> None:
        value = base_contract()
        value["textures"][0]["modes"] = ["chrome", "additive"]
        normalized = validate_contract(value)
        self.assertEqual(normalized["outputs"]["sven_mdl"], "unit_model.mdl")
        self.assertEqual(normalized["outputs"]["qc"], "unit_model.qc")
        self.assertIn('$texrendermode "base.bmp" chrome', render_qc(value))

    def test_bone_renames_are_validated_and_rendered_before_models(self) -> None:
        value = base_contract()
        value["bones"] = [{"name": "target", "parent": None}]
        value["hitboxes"][0]["bone"] = "target"
        value["bone_renames"] = [{"source": "source", "target": "target"}]
        qc = render_qc(value)
        self.assertLess(qc.index('$renamebone "source" "target"'), qc.index('$body "body"'))
        self.assert_contract_error(
            lambda contract: contract.update(bone_renames=[{"source": "root", "target": "root"}]),
            "rename a bone to itself",
        )

    def test_bone_rename_chains_and_missing_targets_are_rejected(self) -> None:
        value = base_contract()
        value["bones"] = [{"name": "final", "parent": None}, {"name": "middle", "parent": "final"}]
        value["bone_renames"] = [
            {"source": "old", "target": "middle"},
            {"source": "middle", "target": "final"},
        ]
        with self.assertRaisesRegex(ContractError, "chains or cycles"):
            validate_contract(value)
        self.assert_contract_error(
            lambda contract: contract.update(bone_renames=[{"source": "old", "target": "missing"}]),
            "absent from final contract bones",
        )

    def test_texture_profiles_and_legacy_chrome_name_are_deterministic(self) -> None:
        value = base_contract()
        value["textures"][0].update(name="CHROME_shell.bmp", source="CHROME_shell.bmp", width=128, height=128)
        with self.assertRaisesRegex(ContractError, "must be 64x64"):
            validate_contract(value)
        value["target_profile"] = "sven-coop"
        normalized = validate_contract(value)
        self.assertEqual(effective_texture_modes(normalized["textures"][0]), ["chrome", "flatshade"])

        value = base_contract()
        value["textures"][0]["modes"] = ["fullbright"]
        with self.assertRaisesRegex(ContractError, "Sven/Xash3D-only fullbright"):
            validate_contract(value)
        value["target_profile"] = "sven-coop"
        self.assertEqual(validate_contract(value)["target_profile"], "sven-coop")

    def test_texrendermode_keeps_all_masked_directives_after_additive(self) -> None:
        value = base_contract()
        value["textures"] = [
            {"name": "mask.bmp", "source": "mask.bmp", "width": 64, "height": 64, "modes": ["masked"]},
            {"name": "glow.bmp", "source": "glow.bmp", "width": 64, "height": 64, "modes": ["additive", "chrome"]},
        ]
        qc = render_qc(value)
        self.assertLess(qc.index('$texrendermode "glow.bmp" additive'), qc.index('$texrendermode "mask.bmp" masked'))

    def test_compatibility_requires_safe_relative_baseline(self) -> None:
        self.assert_contract_error(
            lambda contract: contract.update(compatibility={"role": "player", "baseline_mdl": "../barney.mdl"}),
            "must stay inside the artifact directory",
        )
        self.assert_contract_error(
            lambda contract: contract.update(compatibility={"role": "prop", "baseline_mdl": "barney.mdl"}),
            "must be player or npc",
        )

    def test_version_two_requires_traceable_intent(self) -> None:
        value = base_contract()
        value.pop("intent")
        with self.assertRaises(ContractError) as caught:
            validate_contract(value)
        self.assertIn("version 2 contract requires intent", str(caught.exception))

    def test_legacy_version_one_remains_readable(self) -> None:
        value = base_contract()
        value["version"] = 1
        value.pop("intent")
        normalized = validate_contract(value)
        self.assertEqual(normalized["version"], 1)

    def test_rejects_unprovable_requirement_phase(self) -> None:
        value = base_contract()
        value["acceptance"] = {"required_phases": ["environment"]}
        with self.assertRaises(ContractError) as caught:
            validate_contract(value)
        self.assertIn("must be included in acceptance.required_phases", str(caught.exception))

    def test_requirement_source_must_be_verbatim_user_text(self) -> None:
        value = base_contract()
        value["intent"]["requirements"][0]["source"] = "also make it explode"
        with self.assertRaises(ContractError) as caught:
            validate_contract(value)
        self.assertIn("must appear verbatim in intent.request", str(caught.exception))

    def test_accepts_baked_event_chain_and_preserves_sampled_fps(self) -> None:
        value = base_contract()
        value["physics"] = {
            "mode": "baked_event_chain",
            "simulation": {
                "source_fps": 60,
                "sample_step": 2,
                "export_fps": 30,
                "sequence": "idle",
                "max_frame": 120,
            },
            "stages": [{"name": "release", "trigger": {"type": "frame", "frame": 0}, "release": ["root"]}],
            "interactions": [],
        }
        normalized = validate_contract(value)
        self.assertEqual(normalized["physics"]["simulation"]["export_fps"], 30)

    def test_external_sequence_limitations_are_explicit_sequence_names(self) -> None:
        value = base_contract()
        value["limitations"] = {"external_sequence_groups": ["idle"]}
        normalized = validate_contract(value)
        self.assertEqual(normalized["limitations"]["external_sequence_groups"], ["idle"])
        value["limitations"]["external_sequence_groups"] = True
        with self.assertRaisesRegex(ContractError, "external_sequence_groups must be a list"):
            validate_contract(value)

    def test_rejects_sampled_fps_that_changes_duration(self) -> None:
        value = base_contract()
        value["physics"] = {
            "mode": "baked_event_chain",
            "simulation": {"source_fps": 60, "sample_step": 2, "export_fps": 20, "sequence": "idle"},
            "stages": [],
            "interactions": [],
        }
        with self.assertRaises(ContractError) as caught:
            validate_contract(value)
        self.assertIn("export_fps must equal source_fps/sample_step", str(caught.exception))

    def test_rejects_contract_boundaries(self) -> None:
        cases = [
            (lambda c: c["bones"].append({"name": "root", "parent": None}), "duplicate bones name"),
            (lambda c: c["bones"].extend([{"name": "a", "parent": "b"}, {"name": "b", "parent": "a"}]), "cycle"),
            (lambda c: c["bodies"][0].update(source="../escape.smd"), "artifact directory"),
            (lambda c: c["textures"][0].update(width=63), "multiples of 16"),
            (lambda c: c.update(skin_families=[["base.bmp"], ["base.bmp", "base.bmp"]]), "identical lengths"),
            (lambda c: c.update(bodygroups=[{"name": "module", "choices": [{"blank": True, "studio": "x.smd"}]}]), "exactly one"),
            (lambda c: c["sequences"][0].update(fps=0), "fps"),
            (lambda c: c["sequences"][0].update(motion=["BAD"]), "motion axis"),
            (lambda c: c["sequences"][0].update(events=[{"frame": 2, "id": 1}]), "outside its frame range"),
            (lambda c: c.update(controllers=[{"index": 0, "bone": "missing", "type": "YR", "start": -1, "end": 1}]), "missing bone"),
            (lambda c: c["bounds"]["bbox"].update(min=[2, 0, 0]), "min must not exceed"),
            (lambda c: c.update(bones=[{"name": f"bone_{index}", "parent": None} for index in range(129)]), "bone budget"),
        ]
        for mutate, needle in cases:
            with self.subTest(needle=needle):
                self.assert_contract_error(mutate, needle)

    def test_require_files_checks_smd_materials_skeleton_and_textures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "reference.smd").write_text(REFERENCE_SMD, encoding="utf-8")
            (root / "idle.smd").write_text(IDLE_SMD, encoding="utf-8")
            source = root / "source.png"
            Image.new("RGBA", (64, 64), (100, 120, 140, 255)).save(source)
            convert_to_indexed_bmp(source, root / "base.bmp", width=64, height=64)
            normalized = validate_contract(base_contract(), artifact_dir=root, require_files=True)
            self.assertEqual(normalized["model_name"], "unit_model.mdl")

    def test_compiled_vertex_limit_depends_on_target_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lines = [
                "version 1", "nodes", '0 "root" -1', "end", "skeleton", "time 0",
                "0 0 0 0 0 0 0", "end", "triangles",
            ]
            for offset in range(0, 2049, 3):
                lines.append("base.bmp")
                for vertex in range(offset, offset + 3):
                    lines.append(f"0 {vertex} 0 0 0 0 1 0 0")
            lines.append("end")
            (root / "reference.smd").write_text("\n".join(lines) + "\n", encoding="utf-8")
            (root / "idle.smd").write_text(IDLE_SMD, encoding="utf-8")
            source = root / "source.png"
            Image.new("RGBA", (64, 64), (100, 120, 140, 255)).save(source)
            convert_to_indexed_bmp(source, root / "base.bmp", width=64, height=64)
            with self.assertRaisesRegex(ContractError, "compiled vertex budget exceeded"):
                validate_contract(base_contract(), artifact_dir=root, require_files=True)
            sven_contract = base_contract()
            sven_contract["target_profile"] = "sven-coop"
            normalized = validate_contract(sven_contract, artifact_dir=root, require_files=True)
            self.assertEqual(normalized["target_profile"], "sven-coop")


class SmdTests(unittest.TestCase):
    def test_parses_reference_and_bounds(self) -> None:
        document = parse_smd(REFERENCE_SMD)
        self.assertEqual(validate_smd(document, require_triangles=True), [])
        self.assertEqual(document.bounds()["max"], [1.0, 1.0, 0.0])

    def test_rejects_weight_and_frame_failures(self) -> None:
        weighted = REFERENCE_SMD.replace("0 -1 -1 0 0 0 1 0 0", "0 -1 -1 0 0 0 1 0 0 1 1 0.5")
        self.assertTrue(any("influences" in item for item in validate_smd(parse_smd(weighted), require_triangles=True)))
        mismatch = IDLE_SMD.replace("0 0 0 0 0 0 0", "1 0 0 0 0 0 0")
        self.assertTrue(any("bone set" in item for item in validate_smd(parse_smd(mismatch))))

    def test_compiled_vertex_count_deduplicates_position_per_bone(self) -> None:
        document = parse_smd("""version 1
nodes
0 "root" -1
1 "piece" 0
end
skeleton
time 0
0 0 0 0 0 0 0
1 0 0 0 0 0 0
end
triangles
base.bmp
0 0 0 0 0 0 1 0 0
0 1 0 0 0 0 1 1 0
0 0 1 0 0 0 1 0 1
base.bmp
1 0 0 0 0 0 1 0 0
0 1 0 0 0 0 1 1 0
0 0 1 0 0 0 1 0 1
end
""")
        self.assertEqual(compiled_model_vertex_count(document), 4)
    def test_material_extension_is_rejected_by_validator(self) -> None:
        document = parse_smd(REFERENCE_SMD.replace("base.bmp", "base.png"))
        self.assertTrue(any("not a BMP" in item for item in validate_smd(document, require_triangles=True)))


class MdlAnimationDecodeTests(unittest.TestCase):
    def test_decodes_valid_and_repeated_animation_spans(self) -> None:
        data = bytearray(24)
        struct.pack_into("<BB", data, 12, 2, 4)
        struct.pack_into("<2h", data, 14, 10, 20)
        struct.pack_into("<BB", data, 18, 1, 2)
        struct.pack_into("<h", data, 20, 30)
        self.assertEqual(
            [_decode_animation_value(bytes(data), 0, 12, frame) for frame in range(6)],
            [10, 20, 20, 20, 30, 30],
        )

    def test_rejects_invalid_animation_span(self) -> None:
        data = bytearray(16)
        struct.pack_into("<BB", data, 12, 2, 1)
        with self.assertRaisesRegex(ValueError, "invalid animation span"):
            _decode_animation_value(bytes(data), 0, 12, 0)


class TextureTests(unittest.TestCase):
    def test_blender_fallback_ignores_stale_same_path_datablock(self) -> None:
        rgba = []
        for y in range(16):
            for x in range(16):
                rgba.extend((x / 15.0, y / 15.0, 0.25, 1.0))
        image = SimpleNamespace(size=(16, 16), pixels=rgba, users=0)
        images = mock.Mock()
        images.load.return_value = image
        fake_bpy = SimpleNamespace(data=SimpleNamespace(images=images))
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "fresh.png"
            destination = Path(directory) / "fallback.bmp"
            with mock.patch.dict(sys.modules, {"bpy": fake_bpy}):
                _convert_with_blender_image(
                    source,
                    destination,
                    width=16,
                    height=16,
                    masked=False,
                    alpha_threshold=128,
                )
            images.load.assert_called_once_with(str(source), check_existing=False)
            images.remove.assert_called_once_with(image)
            facts = validate_indexed_bmp(destination, width=16, height=16)
            self.assertGreater(len(facts["indices_used"]), 1)

    def test_embedded_fallback_writer_produces_goldsrc_bmp(self) -> None:
        rgba = []
        for y in range(16):
            for x in range(16):
                rgba.extend((x / 15.0, y / 15.0, 0.25, 1.0))
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "fallback.bmp"
            _write_indexed_bmp_from_rgba(
                rgba,
                destination,
                width=16,
                height=16,
                masked=False,
                alpha_threshold=128,
            )
            facts = validate_indexed_bmp(destination, width=16, height=16)
            self.assertEqual(facts["bits_per_pixel"], 8)
            self.assertEqual(facts["palette_entries"], 256)
            self.assertGreater(len(facts["indices_used"]), 1)

    def test_embedded_fallback_encodes_linear_rgb_as_srgb(self) -> None:
        linear_middle_gray = 0.21586
        rgba = [linear_middle_gray, linear_middle_gray, linear_middle_gray, 1.0] * (16 * 16)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "middle_gray.bmp"
            _write_indexed_bmp_from_rgba(
                rgba,
                destination,
                width=16,
                height=16,
                masked=False,
                alpha_threshold=128,
            )
            pixel = Image.open(destination).convert("RGB").getpixel((0, 0))
            self.assertGreaterEqual(min(pixel), 120)
            self.assertLessEqual(max(pixel), 180)

    def test_converter_writes_full_palette_and_masked_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "masked.png"
            image = Image.new("RGBA", (64, 64), (30, 200, 80, 0))
            for y in range(16, 48):
                for x in range(16, 48):
                    image.putpixel((x, y), (30, 200, 80, 255))
            image.save(source)
            facts = convert_to_indexed_bmp(source, root / "masked.bmp", width=64, height=64, modes=["masked"])
            self.assertEqual(facts["palette_entries"], 256)
            self.assertIn(255, facts["indices_used"])
            self.assertEqual(facts["transparent_pixel_count"], 3072)
            self.assertEqual(facts["visible_pixel_count"], 1024)
            self.assertIsNotNone(facts["weighted_mean_luminance"])

    def test_indexed_texture_reports_color_and_luminance_risks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "black.png"
            Image.new("RGBA", (16, 16), (0, 0, 0, 255)).save(source)
            facts = convert_to_indexed_bmp(source, root / "black.bmp", width=16, height=16)
            self.assertEqual(facts["used_color_count"], 1)
            self.assertIn("single_color", facts["risk_labels"])
            self.assertIn("all_visible_pixels_black", facts["risk_labels"])
            self.assertEqual(sum(facts["pixel_frequencies"].values()), 256)

    def test_rejects_nonindexed_and_bad_mask_palette(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rgb = root / "rgb.bmp"
            Image.new("RGB", (64, 64), (1, 2, 3)).save(rgb)
            with self.assertRaises(TextureError):
                validate_indexed_bmp(rgb)
            source = root / "source.png"
            Image.new("RGBA", (64, 64), (1, 2, 3, 0)).save(source)
            destination = root / "masked.bmp"
            convert_to_indexed_bmp(source, destination, width=64, height=64, modes=["masked"])
            data = bytearray(destination.read_bytes())
            palette_start = 14 + struct.unpack_from("<I", data, 14)[0]
            data[palette_start + 255 * 4 : palette_start + 255 * 4 + 3] = b"\x00\x00\x00"
            destination.write_bytes(data)
            with self.assertRaises(TextureError):
                validate_indexed_bmp(destination, modes=["masked"])


class VisualEvidenceTests(unittest.TestCase):
    def test_rejects_dark_high_dynamic_range_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "dark.png"
            image = Image.new("RGB", (128, 128), (2, 3, 4))
            image.putpixel((64, 64), (255, 255, 255))
            image.save(path)
            issues: list[dict] = []
            facts = _preview(path, issues)
            self.assertGreaterEqual(facts["dynamic_range"], 20)
            self.assertLess(facts["mean_luminance"], 16)
            self.assertLess(facts["foreground_ratio"], 0.002)
            self.assertTrue(any(item["code"] == "visual.blank" for item in issues))

    def test_reports_model_region_separately_from_background(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "model.png"
            image = Image.new("RGB", (128, 128), (4, 6, 8))
            for y in range(32, 96):
                for x in range(32, 96):
                    image.putpixel((x, y), (90, 130, 170))
            image.save(path)
            issues: list[dict] = []
            facts = _preview(path, issues)
            self.assertAlmostEqual(facts["foreground_ratio"], 0.25, places=2)
            self.assertEqual(facts["foreground_bbox"], [32, 32, 96, 96])
            self.assertFalse(any(item["severity"] == "error" for item in issues))


class CompilerIntegrationTests(unittest.TestCase):
    def test_stage_clis_wrap_invalid_contract_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract_path = root / "model_contract.json"
            contract_path.write_text("{}\n", encoding="utf-8")
            cases = (
                ("compile_model.py", root / "compile_sven.json"),
                ("inspect_model.py", root / "mdl_inspection.json"),
            )
            for script_name, report_path in cases:
                with self.subTest(script=script_name):
                    completed = subprocess.run(
                        [sys.executable, str(SCRIPT_DIR / script_name), str(contract_path)],
                        capture_output=True, text=True, errors="replace", timeout=30,
                    )
                    self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
                    report = json.loads(report_path.read_text(encoding="utf-8"))
                    self.assertEqual(report["status"], "fail")
                    self.assertEqual(report["error"]["code"], "contract.invalid")

    def test_single_compile_and_structured_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract = base_contract()
            (root / "reference.smd").write_text(REFERENCE_SMD, encoding="utf-8")
            (root / "idle.smd").write_text(IDLE_SMD, encoding="utf-8")
            source = root / "source.png"
            Image.new("RGBA", (64, 64), (100, 120, 140, 255)).save(source)
            convert_to_indexed_bmp(source, root / "base.bmp", width=64, height=64)
            contract_path = root / "model_contract.json"
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            command = [sys.executable, str(SCRIPT_DIR / "compile_model.py"), str(contract_path)]
            completed = subprocess.run(
                command,
                capture_output=True, text=True, errors="replace", timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            normalized = validate_contract(contract)
            inspection = inspect_mdl(root / normalized["outputs"]["sven_mdl"])
            self.assertEqual(validate_mdl_contract(inspection, normalized), [])
            changed = copy.deepcopy(normalized)
            changed["bounds"]["bbox"]["max"][0] = 2
            self.assertTrue(any(item["code"] == "mdl.bbox" for item in validate_mdl_contract(inspection, changed)))

    def test_renamebone_canonicalizes_smd_and_compiled_bone_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract = base_contract()
            contract["bones"] = [{"name": "target", "parent": None}]
            contract["bone_renames"] = [{"source": "source", "target": "target"}]
            contract["hitboxes"][0]["bone"] = "target"
            (root / "reference.smd").write_text(REFERENCE_SMD.replace('"root"', '"source"'), encoding="utf-8")
            (root / "idle.smd").write_text(IDLE_SMD.replace('"root"', '"source"'), encoding="utf-8")
            source = root / "source.png"
            Image.new("RGBA", (64, 64), (100, 120, 140, 255)).save(source)
            convert_to_indexed_bmp(source, root / "base.bmp", width=64, height=64)
            contract_path = root / "model_contract.json"
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(SCRIPT_DIR / "compile_model.py"), str(contract_path)],
                capture_output=True, text=True, errors="replace", timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            parsed = inspect_mdl(root / "unit_model.mdl")
            self.assertEqual([bone["name"] for bone in parsed["bones"]], ["target"])

    def test_chrome_name_set_and_flag_set_compile_to_expected_flags(self) -> None:
        cases = (
            ("CHROME_shell.bmp", [], {"chrome", "flatshade"}),
            ("metal.bmp", ["chrome"], {"chrome"}),
        )
        for texture_name, modes, expected in cases:
            with self.subTest(texture=texture_name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                contract = base_contract()
                contract["textures"][0].update(name=texture_name, source=texture_name, modes=modes)
                (root / "reference.smd").write_text(REFERENCE_SMD.replace("base.bmp", texture_name), encoding="utf-8")
                (root / "idle.smd").write_text(IDLE_SMD, encoding="utf-8")
                source = root / "source.png"
                Image.new("RGBA", (64, 64), (100, 120, 140, 255)).save(source)
                convert_to_indexed_bmp(source, root / texture_name, width=64, height=64)
                contract_path = root / "model_contract.json"
                contract_path.write_text(json.dumps(contract), encoding="utf-8")
                completed = subprocess.run(
                    [sys.executable, str(SCRIPT_DIR / "compile_model.py"), str(contract_path)],
                    capture_output=True, text=True, errors="replace", timeout=30,
                )
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                parsed = inspect_mdl(root / "unit_model.mdl")
                self.assertTrue(expected <= set(parsed["textures"][0]["flag_names"]))

    def test_unspecified_hitboxes_accept_compiler_generated_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract = base_contract()
            contract["hitboxes"] = []
            (root / "reference.smd").write_text(REFERENCE_SMD, encoding="utf-8")
            (root / "idle.smd").write_text(IDLE_SMD, encoding="utf-8")
            source = root / "source.png"
            Image.new("RGBA", (64, 64), (100, 120, 140, 255)).save(source)
            convert_to_indexed_bmp(source, root / "base.bmp", width=64, height=64)
            contract_path = root / "model_contract.json"
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(SCRIPT_DIR / "compile_model.py"), str(contract_path)],
                capture_output=True, text=True, errors="replace", timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            normalized = validate_contract(contract)
            inspection = inspect_mdl(root / normalized["outputs"]["sven_mdl"])
            self.assertEqual(validate_mdl_contract(inspection, normalized), [])


if __name__ == "__main__":
    unittest.main()
