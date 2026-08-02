"""Runtime API stored in ``bpy.app.driver_namespace``."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4
import hashlib

from .blender.mdl_import import import_mdl as _import_mdl
from .blender.smd_import import import_smd as _import_smd, import_smd_animation as _import_smd_animation
from .core.blender_namespace import assert_exact_asset_namespace, purge_asset_namespace
from .core.compatibility import validate_model_compatibility, validate_player_portrait
from .core.decompile import decompile_mdl
from .core.errors import ToolchainError
from .core.mdl_v10 import inspect_mdl
from .core.model_contract import load_contract
from .core.paths import ensure_outside_skill_tree
from .core.physics_config import configure_rigidbody_world
from .core.rigidbody_bake import (
    apply_rigidbody_world_transform,
    audit_armature_rigidbody_transfer,
    audit_smd_rigidbody_transfer,
    evaluate_physics_event_chain,
    run_adaptive_simulation,
    write_capture_matrices,
)
from .core.stages import PUBLIC_STAGES, execute_stage
from .core.textures import convert_to_indexed_bmp


def _guard(phase: str, code: str, function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except ToolchainError:
        raise
    except Exception as exc:
        details = {"type": type(exc).__name__}
        if isinstance(getattr(exc, "report", None), dict):
            details["report"] = exc.report
        raise ToolchainError(
            phase, code, str(exc), details,
        ) from exc


class RuntimeAPI:
    def __init__(self) -> None:
        self._captures = {}

    def capabilities(self) -> dict:
        compiler = Path(__file__).resolve().parent / "bin" / "windows-x64" / "studiomdl.exe"
        return {
            "id": "goldsrc_model_toolchain",
            "version": "1.3.2",
            "api_version": 1,
            "blender": "5.2.x",
            "platform": "windows-x64",
            "distribution": "public_github_release",
            "repository": "https://github.com/XiangXtreme/goldsrc-model-toolchain",
            "release": "v1.3.2",
            "stages": list(PUBLIC_STAGES),
            "formats": {"smd": 1, "mdl": 10, "qc": True, "indexed_bmp": True},
            "roundtrip_parser": "SourceIO 5.5.4 derived GoldSrc-only reader",
            "components": {
                "studiomdl": {
                    "bundled": compiler.is_file(),
                    "sha256": hashlib.sha256(compiler.read_bytes()).hexdigest() if compiler.is_file() else None,
                    "source": "Sven Co-op SDK modelling tools",
                },
                "pillow": {"version": "12.3.0", "license": "HPND"},
                "sourceio_reader": {"version": "5.5.4-derived", "license": "MIT"},
            },
            "external_sequence_groups": "explicit_contract_limitation_required",
            "features": {
                "bone_renames": True,
                "model_compatibility": ["player", "npc"],
                "player_portrait": True,
                "profile_texture_rules": True,
                "texrendermode_ordering": True,
                "multi_source_sequence_authoring": False,
                "smd_animation_binding": True,
                "mdl_decompile": True,
                "roundtrip_matrix_audit": True,
                "smd_declaration_order_audit": True,
                "loop_endpoint_validation": True,
                "bounds_aware_roundtrip_camera": True,
                "blank_preview_rejection": True,
                "preflight_object_bounds": True,
                "high_quality_texture_quantization": "Pillow MEDIANCUT",
                "texture_fidelity_report": True,
                "profile_model_budgets": {
                    "compiled_vertices": 2048,
                    "compiled_normals": 2048,
                    "triangles": 20000,
                },
            },
            "rigidbody_capture": "opaque_handle_with_json_matrix_access",
            "artifact_policy": "outside_any_skill_tree",
            "ui": False,
        }

    def execute_stage(self, stage, contract_path, artifacts_dir) -> dict:
        return execute_stage(stage, contract_path, artifacts_dir)

    def import_smd(self, path, *, scale=1.0, action_name=None) -> dict:
        return _guard("IMPORT", "smd.import", _import_smd, path, scale=float(scale), action_name=action_name)

    def import_smd_animation(
        self, animation_smd, *, reference_smd=None, target_armature=None,
        scale=1.0, action_name=None,
    ) -> dict:
        return _guard(
            "IMPORT", "smd.animation_import", _import_smd_animation,
            animation_smd, reference_smd=reference_smd, target_armature=target_armature,
            scale=float(scale), action_name=action_name,
        )

    def import_mdl(self, path, *, scale=1.0, reset_scene=False) -> dict:
        result = _guard(
            "IMPORT", "mdl.import", _import_mdl, path,
            scale=float(scale), reset_scene=bool(reset_scene),
        )
        return {
            "path": result["path"],
            "armature": result["armature"].name,
            "objects": [obj.name for obj in result["objects"]],
            "materials": [material.name for material in result["materials"]],
            "actions": [action.name for action in result["actions"]],
            "bodygroups": result["bodygroups"],
            "skin_families": result["skin_families"],
            "external_sequence_groups": result["external_sequence_groups"],
            "action_matrix_audits": result["action_matrix_audits"],
        }

    def convert_texture(self, source, destination, *, width, height, modes=(), alpha_threshold=128) -> dict:
        destination = _guard(
            "TEXTURE", "artifacts.skill_root", ensure_outside_skill_tree,
            destination, label="Texture destination",
        )
        return _guard(
            "TEXTURE", "texture.convert", convert_to_indexed_bmp,
            source, destination, width=int(width), height=int(height), modes=list(modes),
            alpha_threshold=int(alpha_threshold),
        )

    def compile_contract(self, contract_path, artifacts_dir) -> dict:
        return execute_stage("COMPILE", contract_path, artifacts_dir)

    def inspect_mdl(self, path) -> dict:
        return _guard("INSPECT", "mdl.inspect", inspect_mdl, Path(path))

    def decompile_mdl(self, mdl_path, artifacts_dir) -> dict:
        artifacts_dir = _guard(
            "DECOMPILE", "artifacts.skill_root", ensure_outside_skill_tree,
            artifacts_dir, label="Decompile artifact directory",
        )
        return _guard(
            "DECOMPILE", "mdl.decompile", decompile_mdl,
            mdl_path, artifacts_dir,
        )

    def validate_model_compatibility(self, candidate_mdl, baseline_mdl, role) -> dict:
        return _guard(
            "INSPECT", "compatibility.model", validate_model_compatibility,
            candidate_mdl, baseline_mdl, role,
        )

    def validate_player_portrait(self, path, *, remapped=False) -> dict:
        return _guard(
            "TEXTURE", "compatibility.player_portrait", validate_player_portrait,
            path, remapped=bool(remapped),
        )

    def load_contract(self, path, *, artifacts_dir=None, require_files=False) -> dict:
        return _guard(
            "CONTRACT", "contract.load", load_contract, path,
            artifact_dir=artifacts_dir, require_files=bool(require_files),
        )

    def configure_rigidbody_world(self, *args, **kwargs) -> dict:
        return _guard("PHYSICS", "rigidbody.configure", configure_rigidbody_world, *args, **kwargs)

    def bake_rigidbody(self, *args, **kwargs) -> dict:
        capture = _guard("PHYSICS", "rigidbody.bake", run_adaptive_simulation, *args, **kwargs)
        capture_id = uuid4().hex
        self._captures[capture_id] = capture
        return {
            "capture_id": capture_id,
            "report": capture.report,
            "animation_bounds": [float(value) for value in capture.animation_bounds],
            "frames": [min(capture.matrices), max(capture.matrices)],
            "frame_count": len(capture.matrices),
            "objects": sorted(next(iter(capture.matrices.values()))) if capture.matrices else [],
        }

    def _capture(self, capture_id):
        try:
            return self._captures[str(capture_id)]
        except KeyError as exc:
            raise ToolchainError(
                "PHYSICS", "rigidbody.capture", "Unknown or released rigid-body capture",
                {"capture_id": str(capture_id)},
            ) from exc

    def capture_matrices(self, capture_id) -> dict:
        capture = self._capture(capture_id)
        return {
            "capture_id": str(capture_id),
            "frame_range": [capture.frame_start, capture.frame_end],
            "matrices": {
                str(frame): {
                    name: [[float(value) for value in row] for row in matrix]
                    for name, matrix in objects.items()
                }
                for frame, objects in sorted(capture.matrices.items())
            },
        }

    def write_capture_matrices(self, capture_id, output_path) -> dict:
        output_path = _guard(
            "PHYSICS", "artifacts.skill_root", ensure_outside_skill_tree,
            output_path, label="Capture output",
        )
        path = _guard(
            "PHYSICS", "rigidbody.capture_write", write_capture_matrices,
            self._capture(capture_id), output_path,
        )
        return {"capture_id": str(capture_id), "path": str(path)}

    def apply_rigidbody_world_transform(
        self, capture_id, pose_bone, object_name, frame, *, initial_frame=None,
        armature_world=None, parent_pose_matrix=None,
    ) -> dict:
        capture = self._capture(capture_id)
        start = capture.frame_start if initial_frame is None else int(initial_frame)
        current = int(frame)
        name = str(object_name)
        try:
            initial_matrix = capture.matrices[start][name]
            current_matrix = capture.matrices[current][name]
        except KeyError as exc:
            raise ToolchainError(
                "PHYSICS", "rigidbody.capture_matrix", "Capture matrix is missing",
                {"capture_id": str(capture_id), "object": name, "initial_frame": start, "frame": current},
            ) from exc
        target = _guard(
            "PHYSICS", "rigidbody.transfer", apply_rigidbody_world_transform,
            pose_bone, initial_matrix, current_matrix,
            armature_world=armature_world, parent_pose_matrix=parent_pose_matrix,
        )
        return {
            "capture_id": str(capture_id), "object": name, "frame": current,
            "target_pose_matrix": [[float(value) for value in row] for row in target],
        }

    def audit_armature_rigidbody_transfer(
        self, capture_id, scene, mesh_object, armature, action, frame_map, object_names,
        *, position_tolerance=0.0005,
    ) -> dict:
        capture = self._capture(capture_id)
        return _guard(
            "PHYSICS", "rigidbody.transfer_audit", audit_armature_rigidbody_transfer,
            scene, mesh_object, armature, action, capture.matrices, frame_map, object_names,
            position_tolerance=float(position_tolerance),
        )

    def audit_smd_rigidbody_transfer(
        self, capture_id, reference_path, animation_path, frame_map, object_names,
        *, world_to_smd=None, position_tolerance=0.002,
    ) -> dict:
        capture = self._capture(capture_id)
        return _guard(
            "PHYSICS", "rigidbody.smd_audit", audit_smd_rigidbody_transfer,
            reference_path, animation_path, capture.matrices, frame_map, object_names,
            world_to_smd=world_to_smd, position_tolerance=float(position_tolerance),
        )

    def evaluate_physics_event_chain(self, capture_id, physics, final_report=None) -> dict:
        return _guard(
            "PHYSICS", "physics.events", evaluate_physics_event_chain,
            self._capture(capture_id), physics, final_report=final_report,
        )

    def release_rigidbody_capture(self, capture_id) -> dict:
        capture = self._capture(capture_id)
        del self._captures[str(capture_id)]
        return {
            "status": "pass", "capture_id": str(capture_id),
            "frame_range": [capture.frame_start, capture.frame_end],
        }

    def purge_asset_namespace(self, *args, **kwargs) -> dict:
        return _guard("NAMESPACE", "namespace.purge", purge_asset_namespace, *args, **kwargs)

    def assert_exact_asset_namespace(self, *args, **kwargs) -> dict:
        return _guard("NAMESPACE", "namespace.assert", assert_exact_asset_namespace, *args, **kwargs)
