"""High-level five-stage execution for the live Blender Runtime API."""

from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..core.errors import ToolchainError
from ..core.model_contract import load_contract
from ..core.paths import ensure_outside_skill_tree, resolve_artifact_root
from ..core.reporting import (
    STAGE_REPORT_NAMES,
    failure_report,
    resolve_report_path,
    summarize_stage_report,
    write_json,
)
from ..core.stages import PUBLIC_STAGES, execute_stage
from .isolated_roundtrip import run_isolated_roundtrip


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stable_roundtrip_facts(report: dict[str, Any]) -> dict[str, Any]:
    facts = report.get("facts", {})
    keys = (
        "meshes", "bones", "textures", "actions", "bodygroups", "skin_family_count",
        "action_matrix_audits", "weighted_vertex_audit", "preview_pixel_hashes",
        "contact_sheet_pixel_hashes", "preview_visibility",
        "canonical_preview_pixel_hash",
    )
    return {key: facts.get(key) for key in keys}


def _pipeline_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report["status"],
        "mdl": report.get("mdl"),
        "report_directory": report["report_directory"],
        "warnings": report.get("warnings", []),
        "issues": report.get("issues", []),
        "known_blockers": report.get("known_blockers", []),
        "failed_stage": report.get("failed_stage"),
        "error": report.get("error"),
        "facts": report.get("facts", {}),
        "stages": {
            name: {
                "status": value.get("status"),
                "duration_seconds": value.get("duration_seconds"),
                "report_path": value.get("report_path"),
            }
            for name, value in report.get("stages", {}).items()
        },
        "delivery": report.get("delivery"),
    }


def _deliver(
    mdl: Path,
    delivery_dir: str | Path | None,
    *,
    replace_existing: bool,
) -> dict[str, Any] | None:
    if delivery_dir is None:
        return None
    destination_root = ensure_outside_skill_tree(delivery_dir, label="MDL delivery directory")
    destination_root.mkdir(parents=True, exist_ok=True)
    destination = destination_root / mdl.name
    if destination.exists() and not replace_existing:
        raise ToolchainError(
            "DELIVERY", "delivery.exists", "Delivery MDL already exists",
            {"path": str(destination)},
        )
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        shutil.copyfile(mdl, temporary)
        temporary.replace(destination)
    except OSError as exc:
        raise ToolchainError(
            "DELIVERY", "delivery.copy", "Could not atomically deliver the MDL",
            {"source": str(mdl), "destination": str(destination), "error": str(exc)},
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)
    source_hash = _sha256(mdl)
    delivered_hash = _sha256(destination)
    if delivered_hash != source_hash:
        raise ToolchainError(
            "DELIVERY", "delivery.hash", "Delivered MDL differs from compiled source",
            {"source": source_hash, "delivered": delivered_hash},
        )
    return {
        "path": str(destination),
        "sha256": delivered_hash,
        "bytes": destination.stat().st_size,
    }


def execute_pipeline(
    contract_path: str | Path,
    artifacts_dir: str | Path,
    *,
    assurance: str = "standard",
    detail_level: str = "summary",
    preserve_author_session: bool = True,
    visual_compare: bool = True,
    delivery_dir: str | Path | None = None,
    replace_delivery: bool = False,
    package_name: str,
) -> dict[str, Any]:
    if assurance not in {"standard", "strict"}:
        raise ToolchainError(
            "PIPELINE", "pipeline.assurance", "assurance must be standard or strict",
            {"assurance": assurance},
        )
    if detail_level not in {"summary", "full"}:
        raise ToolchainError(
            "PIPELINE", "pipeline.detail_level", "detail_level must be summary or full",
            {"detail_level": detail_level},
        )
    root = resolve_artifact_root(artifacts_dir)
    root.mkdir(parents=True, exist_ok=True)
    report_dir = root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    pipeline_path = report_dir / "pipeline.json"
    stage_reports: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    full_reports: dict[str, dict[str, Any]] = {}
    failed_stage = None
    error = None
    author_preview = None
    visual_report = None
    started_pipeline = time.perf_counter()

    for stage in PUBLIC_STAGES:
        if stage == "ROUNDTRIP" and visual_compare and not preserve_author_session:
            visual_started = time.perf_counter()
            visual_path = report_dir / "visual_compare.json"
            try:
                from .visual_compare import create_static_author_preview

                author_preview = create_static_author_preview(contract_path, root)
            except ToolchainError as exc:
                failed_stage = "VISUAL_COMPARE"
                error = exc.as_dict()
                visual_report = failure_report(exc, stage="VISUAL")
                write_json(visual_path, visual_report)
                issues.append({
                    "severity": "error", "code": exc.code, "message": exc.message,
                })
                stage_reports["VISUAL_COMPARE"] = {
                    "status": "fail", "phase": "visual_review",
                    "duration_seconds": round(time.perf_counter() - visual_started, 3),
                    "report_path": str(visual_path),
                }
                break
            except Exception as exc:
                wrapped = ToolchainError(
                    "VISUAL", "visual.author_unhandled", str(exc),
                    {"type": type(exc).__name__},
                )
                failed_stage = "VISUAL_COMPARE"
                error = wrapped.as_dict()
                visual_report = failure_report(wrapped, stage="VISUAL")
                write_json(visual_path, visual_report)
                issues.append({
                    "severity": "error", "code": wrapped.code, "message": wrapped.message,
                })
                stage_reports["VISUAL_COMPARE"] = {
                    "status": "fail", "phase": "visual_review",
                    "duration_seconds": round(time.perf_counter() - visual_started, 3),
                    "report_path": str(visual_path),
                }
                break
        started = time.perf_counter()
        path = resolve_report_path(root, stage=stage)
        try:
            if stage == "ROUNDTRIP" and preserve_author_session:
                result = run_isolated_roundtrip(
                    contract_path, root,
                    evidence_dir=root / "roundtrip" / "primary",
                    package_name=package_name,
                )
            else:
                result = execute_stage(stage, contract_path, root)
            write_json(path, result)
            summary = summarize_stage_report(stage, result, path)
            summary["duration_seconds"] = round(time.perf_counter() - started, 3)
            stage_reports[stage] = summary
            full_reports[stage] = result
            warnings.extend(summary["warnings"])
            issues.extend(summary["issues"])
            blockers.extend(summary["known_blockers"])
            if result.get("status") not in {"pass", "pass_with_known_blockers"}:
                failed_stage = stage
                error = result.get("error") or {
                    "phase": stage,
                    "code": "pipeline.stage_status",
                    "message": "Stage did not pass",
                    "details": {"status": result.get("status")},
                }
                break
        except ToolchainError as exc:
            failed_stage = stage
            error = exc.as_dict()
            failure = failure_report(exc, stage=stage)
            write_json(path, failure)
            summary = summarize_stage_report(stage, failure, path)
            summary["duration_seconds"] = round(time.perf_counter() - started, 3)
            stage_reports[stage] = summary
            issues.extend(summary["issues"])
            break
        except Exception as exc:
            wrapped = ToolchainError(
                stage, "pipeline.stage_unhandled", str(exc), {"type": type(exc).__name__},
            )
            failed_stage = stage
            error = wrapped.as_dict()
            failure = failure_report(wrapped, stage=stage)
            write_json(path, failure)
            summary = summarize_stage_report(stage, failure, path)
            summary["duration_seconds"] = round(time.perf_counter() - started, 3)
            stage_reports[stage] = summary
            issues.extend(summary["issues"])
            break

    strict_report = None
    if failed_stage is None and assurance == "strict":
        strict_path = report_dir / "sourceio_roundtrip_repeat.json"
        strict_started = time.perf_counter()
        try:
            strict_report = run_isolated_roundtrip(
                contract_path, root,
                evidence_dir=root / "roundtrip" / "repeat",
                package_name=package_name,
            )
            write_json(strict_path, strict_report)
            first = _stable_roundtrip_facts(full_reports["ROUNDTRIP"])
            second = _stable_roundtrip_facts(strict_report)
            differing = sorted(key for key in first if first[key] != second[key])
            if differing:
                raise ToolchainError(
                    "ROUNDTRIP", "pipeline.roundtrip_nondeterministic",
                    "Repeated isolated readback changed structural or decoded-pixel evidence",
                    {"differing_fields": differing},
                )
            stage_reports["ROUNDTRIP_REPEAT"] = {
                "status": "pass", "phase": "sourceio_roundtrip",
                "duration_seconds": round(time.perf_counter() - strict_started, 3),
                "report_path": str(strict_path),
            }
        except ToolchainError as exc:
            failed_stage = "ROUNDTRIP_REPEAT"
            error = exc.as_dict()
            write_json(strict_path, strict_report or failure_report(exc, stage="ROUNDTRIP"))
            issues.append({
                "severity": "error", "code": exc.code, "message": exc.message,
            })
        except Exception as exc:
            wrapped = ToolchainError(
                "ROUNDTRIP", "pipeline.strict_unhandled", str(exc),
                {"type": type(exc).__name__},
            )
            failed_stage = "ROUNDTRIP_REPEAT"
            error = wrapped.as_dict()
            write_json(strict_path, strict_report or failure_report(wrapped, stage="ROUNDTRIP"))
            issues.append({
                "severity": "error", "code": wrapped.code, "message": wrapped.message,
            })

    if failed_stage is None and visual_compare:
        visual_path = report_dir / "visual_compare.json"
        try:
            from .visual_compare import create_static_visual_comparison

            visual_report = create_static_visual_comparison(
                contract_path, root, full_reports["ROUNDTRIP"], full_reports["EXPORT"],
                author_preview=author_preview,
            )
            write_json(visual_path, visual_report)
            if visual_report.get("status") != "pass":
                raise ToolchainError(
                    "VISUAL", "visual.compare", "Author/readback visual comparison failed",
                    {"checks": visual_report.get("checks", {})},
                )
            stage_reports["VISUAL_COMPARE"] = {
                "status": "pass", "phase": "visual_review",
                "duration_seconds": None, "report_path": str(visual_path),
            }
        except ToolchainError as exc:
            failed_stage = "VISUAL_COMPARE"
            error = exc.as_dict()
            write_json(visual_path, visual_report or failure_report(exc, stage="VISUAL"))
            issues.append({
                "severity": "error", "code": exc.code, "message": exc.message,
            })
        except Exception as exc:
            wrapped = ToolchainError(
                "VISUAL", "visual.unhandled", str(exc), {"type": type(exc).__name__},
            )
            failed_stage = "VISUAL_COMPARE"
            error = wrapped.as_dict()
            write_json(visual_path, failure_report(wrapped, stage="VISUAL"))
            issues.append({
                "severity": "error", "code": wrapped.code, "message": wrapped.message,
            })

    contract = None
    mdl = None
    delivery = None
    if failed_stage is None:
        try:
            contract = load_contract(contract_path, artifact_dir=root, require_files=False)
            mdl = (root / contract["outputs"]["sven_mdl"]).resolve()
            delivery = _deliver(mdl, delivery_dir, replace_existing=replace_delivery)
        except ToolchainError as exc:
            failed_stage = "DELIVERY"
            error = exc.as_dict()
            issues.append({
                "severity": "error", "code": exc.code, "message": exc.message,
            })

    export_facts = stage_reports.get("EXPORT", {}).get("facts", {})
    compile_facts = stage_reports.get("COMPILE", {}).get("facts", {})
    facts = {
        "author_triangles": export_facts.get("author_triangles"),
        "crossed_tile_triangles": export_facts.get("crossed_tile_triangles", 0),
        "post_tile_triangles": export_facts.get("post_tile_triangles"),
        "triangles": export_facts.get("post_tile_triangles") or export_facts.get("author_triangles"),
        "material_mapping_audit": (
            "pass"
            if export_facts.get("material_audits")
            and all(item.get("status") == "pass" for item in export_facts["material_audits"])
            else None
        ),
        "compiled_tiles": export_facts.get("compiled_textures"),
        "omitted_tiles": export_facts.get("omitted_textures"),
        "textures": compile_facts.get("textures"),
        "mdl_bytes": mdl.stat().st_size if mdl is not None and mdl.is_file() else None,
        "mdl_sha256": _sha256(mdl) if mdl is not None and mdl.is_file() else None,
    }
    report = {
        "status": "pass" if failed_stage is None else "fail",
        "assurance": assurance,
        "duration_seconds": round(time.perf_counter() - started_pipeline, 3),
        "contract": str(Path(contract_path).expanduser().resolve()),
        "artifacts": str(root),
        "report_directory": str(report_dir),
        "mdl": str(mdl) if mdl is not None and mdl.is_file() else None,
        "warnings": warnings,
        "issues": issues,
        "known_blockers": blockers,
        "failed_stage": failed_stage,
        "error": error,
        "facts": facts,
        "stages": stage_reports,
        "strict_roundtrip": {
            "performed": assurance == "strict",
            "report_path": str(report_dir / "sourceio_roundtrip_repeat.json") if assurance == "strict" else None,
        },
        "visual_compare": visual_report,
        "delivery": delivery,
    }
    write_json(pipeline_path, report)
    return report if detail_level == "full" else _pipeline_summary(report)
