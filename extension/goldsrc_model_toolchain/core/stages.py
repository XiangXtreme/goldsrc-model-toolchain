"""Single-stage execution shared by the Blender operator and host CLIs."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .compatibility import compare_model_compatibility
from .errors import ToolchainError
from .mdl_v10 import compare_mdl_sequence_to_smd, inspect_mdl, validate_mdl_contract
from .model_contract import ContractError, load_contract, write_qc
from .paths import resolve_artifact_root, resolve_toolchain
from .smd import animation_budget_hint, read_smd


PUBLIC_STAGES = ("PREFLIGHT", "EXPORT", "COMPILE", "INSPECT", "ROUNDTRIP")


def _requirements(contract: dict, phase: str, summary: str, evidence: dict) -> list[dict]:
    return [
        {"id": item["id"], "status": "pass", "summary": summary, "evidence": evidence}
        for item in contract.get("intent", {}).get("requirements", [])
        if phase in item.get("evidence_phases", [])
    ]


def run_preflight(contract_path: str | Path, artifacts_dir: str | Path) -> dict:
    from .blender_preflight import inspect_scene

    contract = load_contract(contract_path, artifact_dir=artifacts_dir, require_files=False)
    report = inspect_scene(contract)
    report["phase"] = "preflight"
    evidence = report.get("facts", {})
    report["requirement_evidence"] = _requirements(
        contract, "preflight", "Blender 5.2 preflight resolved contract-owned scene data", evidence,
    )
    return report


def run_export(contract_path: str | Path, artifacts_dir: str | Path) -> dict:
    from ..blender.smd_export import export_contract

    return export_contract(contract_path, artifacts_dir)


def run_compile(contract_path: str | Path, artifacts_dir: str | Path) -> dict:
    root = Path(artifacts_dir).expanduser().resolve()
    contract = load_contract(contract_path, artifact_dir=root, require_files=True)
    compiler = resolve_toolchain().sven_studiomdl
    if compiler is None or not compiler.is_file():
        raise ToolchainError("COMPILE", "compile.compiler", "Sven StudioMDL is missing")
    budgets = {
        sequence["name"]: animation_budget_hint(read_smd(root / sequence["source"]))
        for sequence in contract["sequences"]
    }
    qc = write_qc(contract, root)
    try:
        completed = subprocess.run(
            [str(compiler), str(qc)], cwd=root, capture_output=True, text=True,
            errors="replace", timeout=180,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ToolchainError("COMPILE", "compile.process", str(exc), {"compiler": str(compiler)}) from exc
    mdl_path = (root / contract["outputs"]["sven_mdl"]).resolve()
    if completed.returncode or not mdl_path.is_file():
        raise ToolchainError(
            "COMPILE", "compile.failed", "StudioMDL did not produce the contract MDL",
            {
                "returncode": completed.returncode,
                "stdout": completed.stdout[-12000:],
                "stderr": completed.stderr[-12000:],
                "mdl": str(mdl_path),
            },
        )
    inspection = inspect_mdl(mdl_path)
    issues = validate_mdl_contract(inspection, contract)
    if issues:
        raise ToolchainError("COMPILE", "compile.contract", "Compiled MDL violates its contract", {"issues": issues})
    evidence = {
        "compiler": str(compiler), "mdl": str(mdl_path), "returncode": completed.returncode,
        "animation_budget": budgets,
    }
    return {
        "status": "pass",
        "phase": "compile_sven",
        **evidence,
        "qc": str(qc),
        "stdout": completed.stdout[-12000:],
        "stderr": completed.stderr[-12000:],
        "inspection": inspection,
        "issues": [],
        "requirement_evidence": _requirements(
            contract, "compile_sven", "Sven StudioMDL compiled the contract artifacts", evidence,
        ),
    }


def run_inspect(contract_path: str | Path, artifacts_dir: str | Path) -> dict:
    root = Path(artifacts_dir).expanduser().resolve()
    contract = load_contract(contract_path, artifact_dir=root, require_files=True)
    mdl_path = (root / contract["outputs"]["sven_mdl"]).resolve()
    inspection = inspect_mdl(mdl_path)
    issues = validate_mdl_contract(inspection, contract)
    audits = {}
    for sequence in contract["sequences"]:
        audit = compare_mdl_sequence_to_smd(
            mdl_path, root / sequence["source"], sequence["name"], smd_scale=float(contract["scale"]),
        )
        audits[sequence["name"]] = audit
        for message in audit["issues"]:
            issues.append({
                "severity": "error", "code": "mdl.animation_decode",
                "message": f"{sequence['name']}: {message}", "context": audit,
            })
    compatibility_report = None
    compatibility = contract.get("compatibility")
    if isinstance(compatibility, dict):
        baseline_path = (root / compatibility["baseline_mdl"]).resolve()
        compatibility_report = compare_model_compatibility(
            inspection, inspect_mdl(baseline_path), compatibility["role"],
        )
        compatibility_report["candidate_mdl"] = str(mdl_path)
        compatibility_report["baseline_mdl"] = str(baseline_path)
        issues.extend(compatibility_report["issues"])
    if any(item.get("severity", "error") == "error" for item in issues):
        raise ToolchainError("INSPECT", "inspect.contract", "MDL v10 binary inspection failed", {"issues": issues})
    evidence = {
        "mdl": str(mdl_path),
        "bones": inspection["bones"],
        "sequences": inspection["sequences"],
        "textures": inspection["textures"],
        "bodyparts": inspection["bodyparts"],
        "animation_audits": audits,
        "compatibility": compatibility_report,
    }
    return {
        "status": "pass",
        "phase": "mdl_inspect",
        "contract_version": contract["version"],
        "target_profile": contract["target_profile"],
        "issues": issues,
        "known_blockers": [],
        "inspections": {"sven": inspection},
        "animation_audits": {"sven": audits},
        "compatibility": compatibility_report,
        "requirement_evidence": _requirements(
            contract, "mdl_inspect", "Independent MDL v10 inspection matched contract and source SMD motion", evidence,
        ),
    }


def run_roundtrip(contract_path: str | Path, artifacts_dir: str | Path) -> dict:
    from ..blender.roundtrip import run_roundtrip as blender_roundtrip

    return blender_roundtrip(contract_path, artifacts_dir)


def execute_stage(stage: str, contract_path: str | Path, artifacts_dir: str | Path) -> dict:
    normalized = str(stage).upper()
    if normalized not in PUBLIC_STAGES:
        raise ToolchainError("OPERATOR", "stage.unsupported", "Unsupported GoldSrc stage", {"stage": stage})
    try:
        artifacts = resolve_artifact_root(artifacts_dir)
    except ValueError as exc:
        raise ToolchainError(
            normalized, "artifacts.skill_root", str(exc), {"artifacts_dir": str(artifacts_dir)},
        ) from exc
    runners = {
        "PREFLIGHT": run_preflight,
        "EXPORT": run_export,
        "COMPILE": run_compile,
        "INSPECT": run_inspect,
        "ROUNDTRIP": run_roundtrip,
    }
    try:
        return runners[normalized](contract_path, artifacts)
    except ToolchainError:
        raise
    except ContractError as exc:
        raise ToolchainError(normalized, "contract.invalid", str(exc), {"errors": exc.errors}) from exc
    except (OSError, ValueError, RuntimeError) as exc:
        raise ToolchainError(normalized, "stage.exception", str(exc), {"type": type(exc).__name__}) from exc
