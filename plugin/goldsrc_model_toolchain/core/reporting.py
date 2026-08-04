"""Canonical stage reports and compact MCP-facing summaries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from .errors import ToolchainError
from .paths import resolve_artifact_root


STAGE_REPORT_NAMES = {
    "PREFLIGHT": "preflight.json",
    "EXPORT": "export.json",
    "COMPILE": "compile_sven.json",
    "INSPECT": "mdl_inspect.json",
    "ROUNDTRIP": "sourceio_roundtrip.json",
}


def resolve_report_path(
    artifacts_dir: str | Path,
    *,
    stage: str | None = None,
    report_path: str | Path | None = None,
) -> Path:
    """Resolve a report path and keep it inside the explicit artifact root."""

    try:
        root = resolve_artifact_root(artifacts_dir)
    except ValueError as exc:
        raise ToolchainError(
            str(stage or "REPORT"), "artifacts.skill_root", str(exc),
            {"artifacts_dir": str(artifacts_dir)},
        ) from exc
    root.mkdir(parents=True, exist_ok=True)
    if report_path is None:
        normalized = str(stage or "").upper()
        if normalized not in STAGE_REPORT_NAMES:
            raise ToolchainError(
                "REPORT", "report.stage", "A canonical report requires a supported stage",
                {"stage": stage},
            )
        path = root / "reports" / STAGE_REPORT_NAMES[normalized]
    else:
        candidate = Path(report_path).expanduser()
        path = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ToolchainError(
            str(stage or "REPORT"), "report.escape", "Report path must stay inside artifacts_dir",
            {"artifacts_dir": str(root), "report_path": str(path)},
        ) from exc
    return path


def write_json(path: str | Path, value: dict[str, Any]) -> Path:
    """Atomically persist UTF-8 JSON."""

    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    try:
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def failure_report(error: ToolchainError, *, stage: str | None = None) -> dict[str, Any]:
    issue = error.as_dict()
    return {
        "status": "fail",
        "phase": error.phase or str(stage or "UNKNOWN"),
        "error": issue,
        "issues": [issue],
        "known_blockers": [],
    }


def requirement_report_reference() -> dict[str, str]:
    """Reference the authoritative facts once instead of copying them per requirement."""

    return {"report_section": "/facts"}


def _issue_summary(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            key: item.get(key)
            for key in ("severity", "code", "message")
            if item.get(key) is not None
        }
        for item in report.get("issues", [])
        if isinstance(item, dict)
    ]


def _preflight_facts(report: dict[str, Any]) -> dict[str, Any]:
    source = report.get("facts", {})
    meshes = []
    for item in source.get("meshes", []) if isinstance(source, dict) else []:
        if not isinstance(item, dict):
            continue
        surface = item.get("evaluated_surface") or {}
        meshes.append({
            "name": item.get("name"),
            "vertices": item.get("evaluated_vertices", item.get("vertices")),
            "triangles": item.get("evaluated_triangles"),
            "active_uv": surface.get("active_uv") if isinstance(surface, dict) else None,
            "active_render_uv": surface.get("active_render_uv") if isinstance(surface, dict) else None,
            "material_tokens": item.get("material_tokens", []),
            "material_distribution": surface.get("material_distribution", []) if isinstance(surface, dict) else [],
            "unweighted": item.get("unweighted"),
            "multiweighted": item.get("multiweighted"),
            "bounds": item.get("world_bounds"),
        })
    return {
        "meshes": meshes,
        "armatures": source.get("armatures") if isinstance(source, dict) else None,
        "actions": len(source.get("actions", [])) if isinstance(source, dict) else 0,
        "static_material_audit": source.get("static_material_audit") if isinstance(source, dict) else None,
    }


def _export_facts(report: dict[str, Any]) -> dict[str, Any]:
    selection = report.get("texture_selection", {})
    references = report.get("references", [])
    source_triangles = 0
    output_triangles = 0
    crossed_triangles = 0
    parts = 0
    material_audits = []
    for reference in references if isinstance(references, list) else []:
        if not isinstance(reference, dict):
            continue
        prepared = reference.get("prepared", {})
        material_audit = reference.get("static_material_audit")
        if isinstance(material_audit, dict):
            material_audits.append({
                "status": material_audit.get("status"),
                "source_object": material_audit.get("source_object"),
                "prepared_object": material_audit.get("prepared_object"),
                "smd_logical_token_triangles": material_audit.get("smd_logical_token_triangles", {}),
            })
        parts += len(prepared.get("compiled_sources", [])) if isinstance(prepared, dict) else 0
        source_triangles += int(reference.get("triangles", 0) or 0)
        output_triangles += int(prepared.get("triangles", 0) or 0) if isinstance(prepared, dict) else 0
        for tiling in prepared.get("large_texture_tiling", []) if isinstance(prepared, dict) else []:
            if not isinstance(tiling, dict):
                continue
            crossed_triangles += int(tiling.get("crossed_triangles", 0) or 0)
    compiled = selection.get("compiled", []) if isinstance(selection, dict) else []
    omitted = selection.get("omitted_unused_large_tiles", []) if isinstance(selection, dict) else []
    return {
        "references": len(references) if isinstance(references, list) else 0,
        "smd_parts": parts,
        "author_triangles": source_triangles or None,
        "crossed_tile_triangles": crossed_triangles,
        "post_tile_triangles": output_triangles or None,
        "source_triangles": source_triangles or None,
        "output_triangles": output_triangles or None,
        "crossed_triangles": crossed_triangles,
        "compiled_textures": len(compiled),
        "omitted_textures": len(omitted),
        "material_audits": material_audits,
        "export_plan": report.get("export_plan"),
        "qc": report.get("qc"),
    }


def _compile_facts(report: dict[str, Any]) -> dict[str, Any]:
    inspection = report.get("inspection", {})
    return {
        "mdl": report.get("mdl"),
        "qc": report.get("qc"),
        "returncode": report.get("returncode"),
        "bones": len(inspection.get("bones", [])) if isinstance(inspection, dict) else None,
        "sequences": len(inspection.get("sequences", [])) if isinstance(inspection, dict) else None,
        "textures": len(inspection.get("textures", [])) if isinstance(inspection, dict) else None,
        "bodyparts": len(inspection.get("bodyparts", [])) if isinstance(inspection, dict) else None,
    }


def _inspect_facts(report: dict[str, Any]) -> dict[str, Any]:
    inspection = (report.get("inspections") or {}).get("sven", {})
    return {
        "bones": len(inspection.get("bones", [])) if isinstance(inspection, dict) else None,
        "sequences": len(inspection.get("sequences", [])) if isinstance(inspection, dict) else None,
        "textures": len(inspection.get("textures", [])) if isinstance(inspection, dict) else None,
        "bodyparts": len(inspection.get("bodyparts", [])) if isinstance(inspection, dict) else None,
        "animation_audits": len((report.get("animation_audits") or {}).get("sven", {})),
    }


def _roundtrip_facts(report: dict[str, Any]) -> dict[str, Any]:
    facts = report.get("facts", {})
    return {
        "mdl": report.get("mdl"),
        "blend": report.get("blend"),
        "meshes": len(facts.get("meshes", [])) if isinstance(facts, dict) else None,
        "bones": facts.get("bones") if isinstance(facts, dict) else None,
        "textures": facts.get("textures") if isinstance(facts, dict) else None,
        "actions": len(facts.get("actions", [])) if isinstance(facts, dict) else None,
        "previews": len(report.get("previews", [])),
        "contact_sheets": len(report.get("contact_sheets", [])),
        "preview_pixel_hashes": facts.get("preview_pixel_hashes", []) if isinstance(facts, dict) else [],
        "contact_sheet_pixel_hashes": facts.get("contact_sheet_pixel_hashes", []) if isinstance(facts, dict) else [],
    }


def summarize_stage_report(stage: str, report: dict[str, Any], report_path: str | Path) -> dict[str, Any]:
    normalized = str(stage).upper()
    facts = {
        "PREFLIGHT": _preflight_facts,
        "EXPORT": _export_facts,
        "COMPILE": _compile_facts,
        "INSPECT": _inspect_facts,
        "ROUNDTRIP": _roundtrip_facts,
    }.get(normalized, lambda value: dict(value.get("facts", {})))(report)
    return {
        "status": report.get("status", "fail"),
        "phase": report.get("phase", normalized.casefold()),
        "report_path": str(Path(report_path).resolve()),
        "facts": facts,
        "warnings": [item for item in _issue_summary(report) if item.get("severity") == "warning"],
        "issues": [item for item in _issue_summary(report) if item.get("severity") != "warning"],
        "known_blockers": report.get("known_blockers", []),
    }
