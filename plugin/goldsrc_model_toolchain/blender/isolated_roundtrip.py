"""Run destructive MDL readback in a disposable Blender process."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from uuid import uuid4

import bpy

from ..core.errors import ToolchainError
from ..core.paths import resolve_artifact_root


def run_isolated_roundtrip(
    contract_path: str | Path,
    artifacts_dir: str | Path,
    *,
    evidence_dir: str | Path,
    package_name: str,
    timeout: float = 300.0,
) -> dict:
    root = resolve_artifact_root(artifacts_dir)
    evidence = Path(evidence_dir).expanduser().resolve()
    try:
        evidence.relative_to(root)
    except ValueError as exc:
        raise ToolchainError(
            "ROUNDTRIP", "roundtrip.evidence_escape",
            "Round-trip evidence directory must stay inside artifacts_dir",
            {"artifacts_dir": str(root), "evidence_dir": str(evidence)},
        ) from exc
    evidence.mkdir(parents=True, exist_ok=True)
    binary = Path(bpy.app.binary_path).expanduser().resolve()
    if not binary.is_file():
        raise ToolchainError(
            "ROUNDTRIP", "roundtrip.blender_binary",
            "Blender executable is unavailable for isolated readback",
            {"binary_path": str(binary)},
        )
    worker = Path(__file__).with_name("roundtrip_worker.py").resolve()
    temporary = root / "reports" / f".roundtrip_worker_{uuid4().hex}.json"
    temporary.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(binary), "--background", "--factory-startup", "--addons", package_name,
        "--python", str(worker), "--",
        "--package", package_name,
        "--module-root", str(Path(__file__).resolve().parents[2]),
        "--contract", str(Path(contract_path).expanduser().resolve()),
        "--artifacts", str(root),
        "--evidence", str(evidence),
        "--report", str(temporary),
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, errors="replace", timeout=float(timeout),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ToolchainError(
            "ROUNDTRIP", "roundtrip.isolation_start", str(exc),
            {"binary": str(binary), "package": package_name},
        ) from exc
    try:
        report = json.loads(temporary.read_text(encoding="utf-8")) if temporary.is_file() else None
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolchainError(
            "ROUNDTRIP", "roundtrip.worker_report", "Isolated readback wrote an invalid report",
            {"error": str(exc), "stdout": completed.stdout[-4000:], "stderr": completed.stderr[-4000:]},
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)
    if completed.returncode or not isinstance(report, dict) or report.get("status") != "pass":
        error = report.get("error", {}) if isinstance(report, dict) else {}
        raise ToolchainError(
            "ROUNDTRIP", "roundtrip.isolated",
            str(error.get("message") or "Isolated Blender readback failed"),
            {
                "worker_error": error,
                "returncode": completed.returncode,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            },
        )
    return report
