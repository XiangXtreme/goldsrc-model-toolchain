from __future__ import annotations

import copy
import hashlib
import json
import math
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

from goldsrc_toolchain.mdl_v10 import (
    _decode_animation_value,
    compare_mdl_sequence_to_smd,
    inspect_mdl,
    validate_mdl_contract,
)
from goldsrc_toolchain.model_contract import ContractError, effective_texture_modes, render_qc, validate_contract
from goldsrc_toolchain.large_textures import split_smd_document, tile_name, tile_smd_document, write_smd
from goldsrc_toolchain.paths import ensure_outside_skill_tree, resolve_artifact_root
from goldsrc_toolchain.stages import _requirements
from goldsrc_toolchain.smd import (
    SmdError,
    audit_loop_endpoint,
    compiled_model_normal_count,
    compiled_model_vertex_count,
    geometry_budget,
    parse_smd,
    validate_smd,
)
from goldsrc_toolchain.transforms import euler_xyz_rotation_error
from goldsrc_toolchain.visual_evidence import (
    choose_front_axis,
    create_labeled_contact_sheet,
    representative_sample_labels,
    summarize_preview_visibility,
)
from goldsrc_toolchain.textures import (
    TextureError,
    _convert_with_blender_image,
    _write_indexed_bmp_from_rgba,
    convert_image_tile_to_indexed_bmp,
    convert_rgba_to_indexed_bmp,
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

    def test_compiled_vertex_limit_is_hard_for_every_bundled_profile(self) -> None:
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
            for profile in ("half-life-cs", "sven-coop"):
                contract = base_contract()
                contract["target_profile"] = profile
                with self.subTest(profile=profile):
                    with self.assertRaisesRegex(ContractError, "compiled vertex budget exceeded"):
                        validate_contract(contract, artifact_dir=root, require_files=True)


class StageEvidenceTests(unittest.TestCase):
    def test_failed_stage_marks_requirement_evidence_failed(self) -> None:
        contract = {
            "intent": {
                "requirements": [{"id": "mesh", "evidence_phases": ["preflight"]}],
            },
        }

        evidence = _requirements(
            contract,
            "preflight",
            "Blender preflight resolved contract-owned scene data",
            {"meshes": []},
            status="fail",
        )

        self.assertEqual(evidence[0]["status"], "fail")
        self.assertIn("did not pass", evidence[0]["summary"])


class LargeTextureTests(unittest.TestCase):
    def _crossing_document(self):
        return parse_smd(
            'version 1\n'
            'nodes\n0 "root" -1\nend\n'
            'skeleton\ntime 0\n0 0 0 0 0 0 0\nend\n'
            'triangles\n'
            'atlas.bmp\n'
            '0 0 0 0 0 0 1 0.25 0.25\n'
            '0 1 0 0 0 0 1 0.75 0.25\n'
            '0 0 1 0 0 0 1 0.25 0.75\n'
            'end\n'
        )

    def test_large_atlas_tiles_crossing_triangle_and_remaps_uv(self) -> None:
        result = tile_smd_document(
            self._crossing_document(), atlas_name="atlas.bmp", width=1024, height=1024,
        )
        self.assertGreater(result.output_triangles, 1)
        self.assertEqual(set(result.tiles), {tile_name("atlas.bmp", 0, 0), tile_name("atlas.bmp", 1, 0), tile_name("atlas.bmp", 0, 1)})
        self.assertTrue(all(0.0 < value < 1.0 for triangle in result.document.triangles for vertex in triangle.vertices for value in vertex.uv))

    def test_smd_budget_split_preserves_all_triangles(self) -> None:
        document = parse_smd(
            'version 1\n'
            'nodes\n0 "root" -1\nend\n'
            'skeleton\ntime 0\n0 0 0 0 0 0 0\nend\n'
            'triangles\n'
            + "".join(
                f'base.bmp\n0 {index * 3} 0 0 0 0 1 0 0\n'
                f'0 {index * 3 + 1} 0 0 0 0 1 1 0\n'
                f'0 {index * 3} 1 0 0 0 1 0 1\n'
                for index in range(3)
            )
            + 'end\n'
        )
        parts = split_smd_document(document, max_vertices=4, max_normals=4, max_triangles=20000)
        self.assertEqual([len(part.triangles) for part in parts], [1, 1, 1])
        self.assertEqual(sum(len(part.triangles) for part in parts), len(document.triangles))

    def test_large_texture_contract_expands_to_512_tiles(self) -> None:
        contract = base_contract()
        contract["textures"] = []
        contract["large_textures"] = [{
            "name": "atlas.bmp", "image": "Atlas_2K", "width": 2048, "height": 2048,
            "tile_size": 512, "modes": ["nomips"],
        }]
        normalized = validate_contract(contract)
        self.assertEqual(len(normalized["textures"]), 16)
        self.assertEqual(normalized["textures"][0]["width"], 512)
        self.assertIn("export_plan", normalized["outputs"])
        self.assertIn('$texrendermode "atlas_00_00.bmp" nomips', render_qc(normalized))

    def test_large_texture_allows_a_single_atlas_axis(self) -> None:
        contract = base_contract()
        contract["textures"] = []
        contract["large_textures"] = [{
            "name": "atlas.bmp", "image": "Atlas_1Kx512", "width": 1024, "height": 512,
            "tile_size": 512, "modes": ["nomips"],
        }]
        normalized = validate_contract(contract)
        self.assertEqual(len(normalized["textures"]), 2)


class AnimationEvidenceTests(unittest.TestCase):
    def test_rotation_comparison_accepts_equivalent_xyz_euler_channels(self) -> None:
        error = euler_xyz_rotation_error(
            (0.2, math.pi / 2.0, 0.7),
            (-0.5, math.pi / 2.0, 0.0),
        )
        self.assertLess(error, 0.000001)

    def test_loop_endpoint_requires_first_pose_duplication(self) -> None:
        matching = parse_smd(
            'version 1\nnodes\n0 "root" -1\nend\nskeleton\n'
            'time 7\n0 0 0 0 0 0 0\ntime 8\n0 0 0 0 6.283185 0 0\nend\n'
        )
        mismatched = parse_smd(
            'version 1\nnodes\n0 "root" -1\nend\nskeleton\n'
            'time 7\n0 0 0 0 0 0 0\ntime 8\n0 1 0 0 0 0 0\nend\n'
        )
        self.assertEqual(audit_loop_endpoint(matching)["status"], "pass")
        audit = audit_loop_endpoint(mismatched)
        self.assertEqual(audit["status"], "fail")
        self.assertEqual([audit["first_frame"], audit["last_frame"]], [7, 8])
        self.assertEqual(audit["worst_position"]["bone"], "root")

    def test_readback_framing_uses_thinnest_axis_and_rejects_all_blank_previews(self) -> None:
        framing = choose_front_axis((-32, -256, -64), (32, 256, 64))
        self.assertEqual(framing["axis_name"], "X")
        blank = summarize_preview_visibility([
            {"foreground_fraction": 0.0}, {"foreground_fraction": 0.0},
        ])
        self.assertEqual(blank["status"], "fail")
        visible = summarize_preview_visibility([
            {"foreground_fraction": 0.0}, {"foreground_fraction": 0.125},
        ])
        self.assertEqual(visible["status"], "pass")

    def test_contact_sheet_preserves_sources_and_places_labels_outside_images(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sizes = [(240, 80), (80, 240), (160, 160), (320, 120), (120, 320)]
            paths = []
            hashes = []
            for index, size in enumerate(sizes):
                path = root / f"sample_{index}.png"
                image = Image.new("RGBA", size, (30 + index * 35, 80, 190, 90 + index * 30))
                image.save(path)
                paths.append(path)
                hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
            report = create_labeled_contact_sheet(
                [
                    {
                        "path": path,
                        "label": label + " " + "very-long-label-" * 8,
                        "detail": f"spin | Frame {index * 32:04d} " + "detail-" * 12,
                    }
                    for index, (path, label) in enumerate(zip(paths, representative_sample_labels(5)))
                ],
                root / "sheet.png",
                title="Five-point animation readback",
                columns=3,
                tile_width=192,
                tile_height=128,
            )
            self.assertEqual((report["rows"], report["columns"]), (2, 3))
            self.assertEqual(report["tile_size"], [192, 128])
            self.assertEqual(len(report["cells"]), 5)
            self.assertEqual(
                [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths], hashes,
            )
            self.assertTrue(Path(report["path"]).is_file())
            self.assertTrue(Path(report["layout_path"]).is_file())
            self.assertGreater(Path(report["path"]).stat().st_size, 0)
            self.assertEqual(json.loads(Path(report["layout_path"]).read_text(encoding="utf-8"))["sha256"], report["sha256"])
            for cell in report["cells"]:
                self.assertLessEqual(cell["image_rect"][3], cell["caption_rect"][1])
                self.assertGreaterEqual(cell["contained_rect"][0], cell["image_rect"][0])
                self.assertGreaterEqual(cell["contained_rect"][1], cell["image_rect"][1])
                self.assertLessEqual(cell["contained_rect"][2], cell["image_rect"][2])
                self.assertLessEqual(cell["contained_rect"][3], cell["image_rect"][3])
                for key in ("label_bbox", "detail_bbox"):
                    self.assertGreaterEqual(cell[key][0], cell["caption_rect"][0])
                    self.assertGreaterEqual(cell[key][1], cell["caption_rect"][1])
                    self.assertLessEqual(cell[key][2], cell["caption_rect"][2])
                    self.assertLessEqual(cell[key][3], cell["caption_rect"][3])

    def test_contact_sheet_sample_labels_cover_short_and_five_point_sets(self) -> None:
        self.assertEqual(representative_sample_labels(1), ["SAMPLE"])
        self.assertEqual(representative_sample_labels(3), ["START", "MID", "END"])
        self.assertEqual(
            representative_sample_labels(5), ["START", "1/4", "MID", "3/4", "END"],
        )
        with self.assertRaisesRegex(ValueError, "cannot be negative"):
            representative_sample_labels(-1)


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

    def test_compiled_normal_budget_is_independent_and_hard_for_all_profiles(self) -> None:
        lines = [
            "version 1", "nodes", '0 "root" -1', "end", "skeleton", "time 0",
            "0 0 0 0 0 0 0", "end", "triangles",
        ]
        for offset in range(0, 2049, 3):
            lines.append("base.bmp")
            for normal_index in range(offset, offset + 3):
                lines.append(f"0 0 0 0 {normal_index} 1 1 0 0")
        lines.append("end")
        document = parse_smd("\n".join(lines) + "\n")
        self.assertEqual(compiled_model_vertex_count(document), 1)
        self.assertGreater(compiled_model_normal_count(document), 2048)
        for profile in ("half-life-cs", "sven-coop"):
            with self.subTest(profile=profile):
                budget = geometry_budget(document, target_profile=profile)
                self.assertTrue(budget["hard_failure"])
                self.assertGreater(budget["compiled_normals"], budget["normal_limit"])
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

    def test_nonmasked_fallback_owns_index_255_and_preserves_white(self) -> None:
        rgba = [1.0, 1.0, 1.0, 1.0] * (16 * 16)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "white.bmp"
            _write_indexed_bmp_from_rgba(
                rgba, destination, width=16, height=16, masked=False,
                alpha_threshold=128, input_color_space="srgb", row_origin="top-left",
            )
            facts = validate_indexed_bmp(destination, width=16, height=16)
            self.assertEqual(facts["indices_used"], [255])
            self.assertEqual(Image.open(destination).convert("RGB").getpixel((0, 0)), (255, 255, 255))

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

    def test_file_converter_preserves_grayscale_and_top_bottom_orientation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "vertical.png"
            image = Image.new("RGBA", (64, 64))
            for y in range(64):
                value = round(255 * y / 63)
                for x in range(64):
                    image.putpixel((x, y), (value, value, value, 255))
            image.save(source)
            facts = convert_to_indexed_bmp(source, root / "vertical.bmp", width=64, height=64)
            converted = Image.open(root / "vertical.bmp").convert("RGB")
            self.assertLess(converted.getpixel((0, 0))[0], converted.getpixel((0, 63))[0])
            self.assertTrue(all(red == green == blue for red, green, blue in converted.getdata()))
            fidelity = facts["conversion"]["fidelity"]
            self.assertEqual(facts["conversion"]["method"], "pillow_mediancut_file")
            self.assertEqual(fidelity["orientation"]["preferred"], "direct")
            self.assertLessEqual(fidelity["mean_absolute_channel_error"], 1.0)
            self.assertLessEqual(fidelity["max_absolute_channel_error"], 1)

    def test_file_atlas_tile_uses_bottom_origin_tile_coordinates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "atlas.png"
            image = Image.new("RGBA", (1024, 1024), (0, 0, 0, 255))
            for y in range(1024):
                color = (220, 40, 40, 255) if y < 512 else (40, 220, 40, 255)
                for x in range(1024):
                    image.putpixel((x, y), color)
            image.save(source)
            convert_image_tile_to_indexed_bmp(
                source, root / "bottom.bmp", source_width=1024, source_height=1024,
                tile_x=0, tile_y=0,
            )
            convert_image_tile_to_indexed_bmp(
                source, root / "top.bmp", source_width=1024, source_height=1024,
                tile_x=0, tile_y=1,
            )
            self.assertEqual(Image.open(root / "bottom.bmp").convert("RGB").getpixel((0, 0)), (40, 220, 40))
            self.assertEqual(Image.open(root / "top.bmp").convert("RGB").getpixel((0, 0)), (220, 40, 40))

    def test_rgba_converter_declares_bottom_left_linear_input(self) -> None:
        rgba = []
        for bottom_row in range(16):
            value = bottom_row / 15.0
            for _x in range(16):
                rgba.extend((value, value, value, 1.0))
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "buffer.bmp"
            facts = convert_rgba_to_indexed_bmp(
                rgba, destination, width=16, height=16,
                input_color_space="linear", row_origin="bottom-left",
            )
            converted = Image.open(destination).convert("RGB")
            self.assertGreater(converted.getpixel((0, 0))[0], converted.getpixel((0, 15))[0])
            self.assertEqual(facts["conversion"]["input_color_space"], "linear")
            self.assertEqual(facts["conversion"]["source_row_origin"], "bottom-left")
            self.assertEqual(facts["conversion"]["fidelity"]["orientation"]["preferred"], "direct")

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

    def test_export_plan_compiles_split_reference_as_bodyparts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lines = [
                "version 1", "nodes", '0 "root" -1', "end", "skeleton", "time 0",
                "0 0 0 0 0 0 0", "end", "triangles",
            ]
            for vertex in range(0, 2051, 3):
                lines.append("base.bmp")
                for index in range(vertex, vertex + 3):
                    lines.append(f"0 {index} 0 0 0 0 1 0 0")
            lines.append("end")
            document = parse_smd("\n".join(lines) + "\n")
            parts = split_smd_document(document)
            self.assertGreater(len(parts), 1)
            write_smd(parts[0], root / "reference.smd")
            for index, part in enumerate(parts[1:], start=2):
                write_smd(part, root / f"reference_part{index:03d}.smd")
            (root / "idle.smd").write_text(IDLE_SMD, encoding="utf-8")
            source = root / "source.png"
            Image.new("RGBA", (64, 64), (100, 120, 140, 255)).save(source)
            convert_to_indexed_bmp(source, root / "base.bmp", width=64, height=64)
            (root / "export_plan.json").write_text(json.dumps({
                "version": 1,
                "references": [{
                    "contract_source": "reference.smd",
                    "compiled_sources": [
                        "reference.smd",
                        *[f"reference_part{index:03d}.smd" for index in range(2, len(parts) + 1)],
                    ],
                }],
            }), encoding="utf-8")
            contract = base_contract()
            contract["model_name"] = "split_model.mdl"
            contract["outputs"] = {
                "sven_mdl": "split_model.mdl",
                "qc": "split_model.qc",
                "export_plan": "export_plan.json",
            }
            contract_path = root / "model_contract.json"
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(SCRIPT_DIR / "compile_model.py"), str(contract_path)],
                capture_output=True, text=True, errors="replace", timeout=60,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            inspection = inspect_mdl(root / "split_model.mdl")
            self.assertEqual(
                [(item["name"], item["model_count"]) for item in inspection["bodyparts"]],
                [("body_part001", 1), ("body_part002", 1)],
            )

    def test_animation_audit_uses_smd_declaration_order_for_nonzero_start_frame(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract = base_contract()
            contract["sequences"][0].update(frame=[1, 2], loop=False)
            animation = """version 1
nodes
0 "root" -1
end
skeleton
time 1
0 0 0 0 0 0 0
time 2
0 0.5 0 0 0.25 0 0
end
"""
            (root / "reference.smd").write_text(REFERENCE_SMD, encoding="utf-8")
            (root / "idle.smd").write_text(animation, encoding="utf-8")
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
            audit = compare_mdl_sequence_to_smd(root / "unit_model.mdl", root / "idle.smd", "idle")
            self.assertEqual(audit["status"], "pass", audit)
            self.assertEqual(audit["frames_checked"], 2)

    def test_compile_rejects_mismatched_loop_endpoint_before_studiomdl(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract = base_contract()
            contract["sequences"][0]["frame"] = [0, 1]
            animation = IDLE_SMD.replace(
                "0 0 0 0 0 0 0\nend\n",
                "0 0 0 0 0 0 0\ntime 1\n0 1 0 0 0 0 0\nend\n",
            )
            (root / "reference.smd").write_text(REFERENCE_SMD, encoding="utf-8")
            (root / "idle.smd").write_text(animation, encoding="utf-8")
            source = root / "source.png"
            Image.new("RGBA", (64, 64), (100, 120, 140, 255)).save(source)
            convert_to_indexed_bmp(source, root / "base.bmp", width=64, height=64)
            contract_path = root / "model_contract.json"
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(SCRIPT_DIR / "compile_model.py"), str(contract_path)],
                capture_output=True, text=True, errors="replace", timeout=30,
            )
            self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
            report = json.loads((root / "compile_sven.json").read_text(encoding="utf-8"))
            self.assertEqual(report["error"]["code"], "compile.loop_endpoint")

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
