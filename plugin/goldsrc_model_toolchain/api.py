"""Runtime API stored in ``bpy.app.driver_namespace``."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4
import hashlib

from .blender.mdl_import import import_mdl as _import_mdl
from .blender.isolated_roundtrip import run_isolated_roundtrip
from .blender.smd_import import import_smd as _import_smd, import_smd_animation as _import_smd_animation
from .core.blender_namespace import assert_exact_asset_namespace, purge_asset_namespace
from .core.compatibility import validate_model_compatibility, validate_player_portrait
from .core.decompile import decompile_mdl
from .core.errors import ToolchainError
from .core.large_textures import (
    split_smd_document,
    tile_smd_document,
    tile_names,
    validate_large_texture_spec,
    write_smd,
)
from .core.mdl_v10 import inspect_mdl
from .core.model_contract import load_contract
from .core.paths import ensure_outside_skill_tree
from .core.reporting import (
    failure_report,
    resolve_report_path,
    summarize_stage_report,
    write_json,
)
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
from .core.selected_static_export import run_selected_static_export
from .core.smd import read_smd
from .core.textures import convert_image_tile_to_indexed_bmp, convert_to_indexed_bmp
from .core.visual_evidence import create_labeled_contact_sheet


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
        self._static_analyses = {}

    def capabilities(self) -> dict:
        compiler = Path(__file__).resolve().parent / "bin" / "windows-x64" / "studiomdl.exe"
        return {
            "id": "goldsrc_model_toolchain",
            "version": "1.4.1",
            "api_version": 1,
            "blender": "5.2.x",
            "platform": "windows-x64",
            "distribution": "public_github_release",
            "repository": "https://github.com/XiangXtreme/goldsrc-model-toolchain",
            "release": "v1.4.1",
            "public_compatibility_baseline": "v1.4.1",
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
                "roundtrip_decoded_pixel_hash": True,
                "preflight_object_bounds": True,
                "preflight_material_texture_token": True,
                "export_time_triangulation": True,
                "evaluated_uv_material_reports": True,
                "texture_bake_uv_guard": True,
                "failed_requirement_evidence": True,
                "stage_report_persistence": True,
                "summary_stage_results": True,
                "isolated_roundtrip": True,
                "static_selection_analysis": True,
                "non_destructive_static_prepare": True,
                "static_contract_from_scene": True,
                "selected_static_export": True,
                "strict_static_pipeline": True,
                "unified_static_visual_compare": True,
                "evaluated_material_mapping_audit": True,
                "large_texture_tiling": {
                    "source_tile_size": 512,
                    "max_tiles_per_mdl": 64,
                    "method": "UV-clipped 512px indexed BMP tiles",
                    "sparse_compiled_tiles": True,
                },
                "smd_budget_split": {
                    "compiled_vertices": 2048,
                    "compiled_normals": 2048,
                    "triangles": 20000,
                    "method": "deterministic triangle-preserving body parts",
                },
                "high_quality_texture_quantization": "Pillow MEDIANCUT",
                "texture_fidelity_report": True,
                "labeled_visual_contact_sheets": True,
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

    def execute_stage(
        self, stage, contract_path, artifacts_dir, *, detail_level="full",
        report_path=None, preserve_author_session=True,
    ) -> dict:
        normalized = str(stage).upper()
        if detail_level not in {"summary", "full"}:
            raise ToolchainError(
                "OPERATOR", "stage.detail_level", "detail_level must be summary or full",
                {"detail_level": detail_level},
            )
        path = resolve_report_path(
            artifacts_dir, stage=normalized, report_path=report_path,
        )
        try:
            if normalized == "ROUNDTRIP" and bool(preserve_author_session):
                result = run_isolated_roundtrip(
                    contract_path, artifacts_dir,
                    evidence_dir=Path(artifacts_dir).expanduser().resolve() / "roundtrip" / "primary",
                    package_name=__package__,
                )
            else:
                result = execute_stage(normalized, contract_path, artifacts_dir)
        except ToolchainError as exc:
            write_json(path, failure_report(exc, stage=normalized))
            raise
        except Exception as exc:
            wrapped = ToolchainError(
                normalized, "stage.unhandled", str(exc), {"type": type(exc).__name__},
            )
            write_json(path, failure_report(wrapped, stage=normalized))
            raise wrapped from exc
        write_json(path, result)
        return summarize_stage_report(normalized, result, path) if detail_level == "summary" else result

    def execute_pipeline(
        self, contract_path, artifacts_dir, *, assurance="standard", detail_level="summary",
        preserve_author_session=True, visual_compare=True, delivery_dir=None,
        replace_delivery=False,
    ) -> dict:
        from .blender.pipeline import execute_pipeline

        return execute_pipeline(
            contract_path, artifacts_dir, assurance=assurance, detail_level=detail_level,
            preserve_author_session=bool(preserve_author_session),
            visual_compare=bool(visual_compare), delivery_dir=delivery_dir,
            replace_delivery=bool(replace_delivery), package_name=__package__,
        )

    def analyze_selected_static(self, object_name=None) -> dict:
        from .blender.static_export import analyze_selected_static

        result, analysis = analyze_selected_static(object_name)
        self._static_analyses[result["analysis_id"]] = analysis
        return result

    def prepare_static_export(
        self, analysis_id, *, artifacts_dir, model_name, request,
        texture_size=None, uv_strategy=None, uv_layer=None,
        origin_strategy=None, bake_mode=None, goldsrc_modes=None,
        target_profile="half-life-cs",
    ) -> dict:
        from .blender.static_export import prepare_static_export

        analysis = self._static_analyses.get(str(analysis_id))
        if analysis is None:
            raise ToolchainError(
                "PREPARE", "static.analysis_id",
                "Unknown static analysis_id; analyze_selected_static must run in this Blender session",
                {"analysis_id": str(analysis_id)},
            )
        return prepare_static_export(
            analysis,
            artifacts_dir=artifacts_dir,
            model_name=model_name,
            request=request,
            texture_size=texture_size,
            uv_strategy=uv_strategy,
            uv_layer=uv_layer,
            origin_strategy=origin_strategy,
            bake_mode=bake_mode,
            goldsrc_modes=goldsrc_modes,
            target_profile=target_profile,
        )

    def export_selected_static(
        self, *, artifacts_dir, model_name, request, object_name=None,
        texture_size=None, uv_strategy=None, uv_layer=None,
        origin_strategy=None, bake_mode=None, goldsrc_modes=None,
        target_profile="half-life-cs", assurance="strict",
        preserve_author_session=True, visual_compare=True,
        delivery_dir=None, replace_delivery=False,
    ) -> dict:
        """Export one selected mesh through the complete compact static workflow."""

        session = None
        if preserve_author_session:
            from .blender.static_export import capture_static_export_session

            session = capture_static_export_session()
        try:
            return run_selected_static_export(
                artifacts_dir=artifacts_dir,
                analyze=lambda: self.analyze_selected_static(object_name),
                prepare=lambda analysis: self.prepare_static_export(
                    analysis["analysis_id"],
                    artifacts_dir=artifacts_dir,
                    model_name=model_name,
                    request=request,
                    texture_size=texture_size,
                    uv_strategy=uv_strategy,
                    uv_layer=uv_layer,
                    origin_strategy=origin_strategy,
                    bake_mode=bake_mode,
                    goldsrc_modes=goldsrc_modes,
                    target_profile=target_profile,
                ),
                execute_pipeline=lambda prepared: self.execute_pipeline(
                    prepared["contract_path"],
                    prepared["artifacts_dir"],
                    assurance=assurance,
                    detail_level="summary",
                    preserve_author_session=bool(preserve_author_session),
                    visual_compare=bool(visual_compare),
                    delivery_dir=delivery_dir,
                    replace_delivery=bool(replace_delivery),
                ),
            )
        finally:
            if session is not None:
                from .blender.static_export import restore_static_export_session

                restore_static_export_session(session)

    def create_static_contract_from_scene(
        self, artifacts_dir, model_name, request, *, object_name,
        armature_name, action_name, uv_layer, textures=None,
        large_textures=None, target_profile="half-life-cs",
        contract_path=None, fps=30.0,
    ) -> dict:
        from .blender.static_export import create_static_contract_from_scene

        return create_static_contract_from_scene(
            artifacts_dir,
            model_name,
            request,
            object_name=object_name,
            armature_name=armature_name,
            action_name=action_name,
            uv_layer=uv_layer,
            textures=textures,
            large_textures=large_textures,
            target_profile=target_profile,
            contract_path=contract_path,
            fps=float(fps),
        )

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

    def split_smd_for_goldsrc(
        self, smd_path, output_dir, *, max_vertices=2048, max_normals=2048, max_triangles=20000,
    ) -> dict:
        output_dir = _guard(
            "EXPORT", "artifacts.skill_root", ensure_outside_skill_tree,
            output_dir, label="SMD split output",
        )
        document = _guard("EXPORT", "smd.read", read_smd, smd_path)
        parts = _guard(
            "EXPORT", "smd.split", split_smd_document, document,
            max_vertices=int(max_vertices), max_normals=int(max_normals), max_triangles=int(max_triangles),
        )
        paths = []
        stem = Path(smd_path).stem
        for index, part in enumerate(parts, start=1):
            path = output_dir / f"{stem}_part{index:03d}.smd"
            write_smd(part, path)
            paths.append({
                "path": str(path), "triangles": len(part.triangles),
                "vertices": len({(vertex.bone, *vertex.position) for triangle in part.triangles for vertex in triangle.vertices}),
            })
        return {"status": "pass", "source": str(Path(smd_path).resolve()), "parts": paths}

    def tile_large_texture(
        self, smd_path, image_path, output_dir, *, atlas_name=None, width, height,
        tile_size=512, modes=(), alpha_threshold=128, uv_clamp_factor=None,
        max_vertices=2048, max_normals=2048, max_triangles=20000,
    ) -> dict:
        output_dir = _guard(
            "EXPORT", "artifacts.skill_root", ensure_outside_skill_tree,
            output_dir, label="Large texture output",
        )
        image_path = Path(image_path).expanduser().resolve()
        atlas_name = atlas_name or f"{image_path.stem}.bmp"
        spec = _guard(
            "EXPORT", "large_texture.spec", validate_large_texture_spec,
            {"name": atlas_name, "image": image_path.name, "width": int(width), "height": int(height), "tile_size": int(tile_size)},
        )
        document = _guard("EXPORT", "smd.read", read_smd, smd_path)
        tiled = _guard(
            "EXPORT", "large_texture.smd", tile_smd_document, document,
            atlas_name=str(spec["name"]), width=int(spec["width"]), height=int(spec["height"]),
            tile_size=int(spec["tile_size"]), uv_clamp_factor=float(uv_clamp_factor or 1.0 / 512.0),
        )
        parts = _guard(
            "EXPORT", "smd.split", split_smd_document, tiled.document,
            max_vertices=int(max_vertices), max_normals=int(max_normals), max_triangles=int(max_triangles),
        )
        tiles = []
        for tile_y in range(int(spec["height"]) // int(spec["tile_size"])):
            for tile_x in range(int(spec["width"]) // int(spec["tile_size"])):
                name = tile_names(str(spec["name"]), int(spec["width"]), int(spec["height"]), int(spec["tile_size"]))[
                    tile_y * (int(spec["width"]) // int(spec["tile_size"])) + tile_x
                ]
                path = output_dir / name
                facts = _guard(
                    "EXPORT", "large_texture.bmp", convert_image_tile_to_indexed_bmp,
                    image_path, path, source_width=int(spec["width"]), source_height=int(spec["height"]),
                    tile_x=tile_x, tile_y=tile_y, tile_size=int(spec["tile_size"]), modes=list(modes),
                    alpha_threshold=int(alpha_threshold),
                )
                tiles.append({"name": name, "path": str(path), "facts": facts})
        part_paths = []
        stem = Path(smd_path).stem
        for index, part in enumerate(parts, start=1):
            path = output_dir / f"{stem}_part{index:03d}.smd"
            write_smd(part, path)
            part_paths.append({"path": str(path), "triangles": len(part.triangles)})
        return {
            "status": "pass", "atlas": str(spec["name"]),
            "source_image": str(image_path), "tiles": tiles, "parts": part_paths,
            "original_triangles": tiled.original_triangles,
            "output_triangles": tiled.output_triangles,
            "crossed_triangles": tiled.crossed_triangles,
        }

    def compile_contract(self, contract_path, artifacts_dir) -> dict:
        return self.execute_stage("COMPILE", contract_path, artifacts_dir)

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

    def create_visual_contact_sheet(
        self, items, destination, *, columns=3, title=None,
        tile_width=384, tile_height=384,
    ) -> dict:
        destination = _guard(
            "VISUAL", "artifacts.skill_root", ensure_outside_skill_tree,
            destination, label="Contact sheet destination",
        )
        return _guard(
            "VISUAL", "visual.contact_sheet", create_labeled_contact_sheet,
            items, destination, columns=int(columns), title=title,
            tile_width=int(tile_width), tile_height=int(tile_height),
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
