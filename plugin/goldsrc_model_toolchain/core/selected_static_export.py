"""Compact orchestration for the selected-static MDL product workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .errors import ToolchainError
from .reporting import resolve_report_path, write_json


_WORKFLOW_REPORT = "reports/static_export.json"


def _compact_error(error: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(error, dict):
        return None
    return {
        key: error.get(key)
        for key in ("phase", "code", "message")
        if error.get(key) is not None
    }


def _exception_payload(exc: Exception, *, phase: str) -> dict[str, Any]:
    if isinstance(exc, ToolchainError):
        return exc.as_dict()
    return {
        "phase": phase,
        "code": "static_export.unhandled",
        "message": str(exc),
        "details": {"type": type(exc).__name__},
    }


def _record(path: Path, result: dict[str, Any], **evidence: Any) -> dict[str, Any]:
    write_json(path, {
        "workflow": "selected_static_export",
        **result,
        "evidence": evidence,
    })
    return result


def _failed(
    path: Path,
    *,
    failed_stage: str,
    error: dict[str, Any],
    analysis: dict[str, Any] | None = None,
    prepared: dict[str, Any] | None = None,
    pipeline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pipeline = pipeline or {}
    result = {
        "status": "fail",
        "mdl": pipeline.get("mdl"),
        "report_directory": str(path.parent),
        "warnings": pipeline.get("warnings", []),
        "facts": pipeline.get("facts", {}),
        "failed_stage": failed_stage,
        "error": _compact_error(error),
    }
    return _record(
        path, result,
        analysis=analysis,
        prepared=prepared,
        pipeline_report=(path.parent / "pipeline.json") if pipeline else None,
        full_error=error,
    )


def run_selected_static_export(
    *,
    artifacts_dir: str | Path,
    analyze: Callable[[], dict[str, Any]],
    prepare: Callable[[dict[str, Any]], dict[str, Any]],
    execute_pipeline: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    """Run analysis, explicit preparation, and validation behind one compact call."""

    try:
        report_path = resolve_report_path(
            artifacts_dir,
            stage="STATIC_EXPORT",
            report_path=_WORKFLOW_REPORT,
        )
    except ToolchainError as exc:
        error = exc.as_dict()
        return {
            "status": "fail",
            "mdl": None,
            "report_directory": None,
            "warnings": [],
            "facts": {},
            "failed_stage": "SETUP",
            "error": _compact_error(error),
        }

    try:
        analysis = analyze()
    except Exception as exc:
        return _failed(
            report_path,
            failed_stage="ANALYZE",
            error=_exception_payload(exc, phase="ANALYZE"),
        )
    if not isinstance(analysis, dict) or analysis.get("status") != "pass":
        error = analysis.get("error") if isinstance(analysis, dict) else None
        return _failed(
            report_path,
            failed_stage="ANALYZE",
            error=error or {
                "phase": "ANALYZE",
                "code": "static_export.analysis_status",
                "message": "Static selection analysis did not pass",
                "details": {
                    "status": analysis.get("status") if isinstance(analysis, dict) else None,
                },
            },
            analysis=analysis if isinstance(analysis, dict) else None,
        )

    try:
        prepared = prepare(analysis)
    except Exception as exc:
        return _failed(
            report_path,
            failed_stage="PREPARE",
            error=_exception_payload(exc, phase="PREPARE"),
            analysis=analysis,
        )
    if not isinstance(prepared, dict):
        return _failed(
            report_path,
            failed_stage="PREPARE",
            error={
                "phase": "PREPARE",
                "code": "static_export.prepare_result",
                "message": "Static preparation returned an invalid result",
                "details": {"type": type(prepared).__name__},
            },
            analysis=analysis,
        )

    if prepared.get("status") == "needs_decision":
        summary = analysis.get("summary", {}) if isinstance(analysis, dict) else {}
        result = {
            "status": "needs_decision",
            "mdl": None,
            "report_directory": str(report_path.parent),
            "warnings": [],
            "facts": {
                key: summary.get(key)
                for key in ("object", "evaluated_vertices", "evaluated_triangles")
                if summary.get(key) is not None
            },
            "analysis_id": prepared.get("analysis_id") or analysis.get("analysis_id"),
            "decisions": prepared.get("decisions", []),
        }
        return _record(report_path, result, analysis=analysis)

    if prepared.get("status") != "pass":
        error = prepared.get("error") or {
            "phase": "PREPARE",
            "code": "static_export.prepare_status",
            "message": "Static preparation did not pass",
            "details": {"status": prepared.get("status")},
        }
        return _failed(
            report_path,
            failed_stage="PREPARE",
            error=error,
            analysis=analysis,
            prepared=prepared,
        )

    try:
        pipeline = execute_pipeline(prepared)
    except Exception as exc:
        return _failed(
            report_path,
            failed_stage="PIPELINE",
            error=_exception_payload(exc, phase="PIPELINE"),
            analysis=analysis,
            prepared=prepared,
        )
    if not isinstance(pipeline, dict):
        return _failed(
            report_path,
            failed_stage="PIPELINE",
            error={
                "phase": "PIPELINE",
                "code": "static_export.pipeline_result",
                "message": "Static export pipeline returned an invalid result",
                "details": {"type": type(pipeline).__name__},
            },
            analysis=analysis,
            prepared=prepared,
        )

    if pipeline.get("status") != "pass":
        error = pipeline.get("error") or {
            "phase": "PIPELINE",
            "code": "static_export.pipeline_status",
            "message": "Static export pipeline did not pass",
            "details": {"status": pipeline.get("status")},
        }
        return _failed(
            report_path,
            failed_stage=str(pipeline.get("failed_stage") or "PIPELINE"),
            error=error,
            analysis=analysis,
            prepared=prepared,
            pipeline=pipeline,
        )

    delivery = pipeline.get("delivery")
    mdl = delivery.get("path") if isinstance(delivery, dict) else pipeline.get("mdl")
    result = {
        "status": "pass",
        "mdl": mdl,
        "report_directory": pipeline.get("report_directory") or str(report_path.parent),
        "warnings": pipeline.get("warnings", []),
        "facts": pipeline.get("facts", {}),
    }
    return _record(
        report_path, result,
        analysis={
            "analysis_id": analysis.get("analysis_id"),
            "summary": analysis.get("summary", {}),
        },
        prepared={
            key: prepared.get(key)
            for key in ("analysis_id", "contract_path", "artifacts_dir", "author_checkpoint", "facts")
        },
        pipeline_report=report_path.parent / "pipeline.json",
    )
