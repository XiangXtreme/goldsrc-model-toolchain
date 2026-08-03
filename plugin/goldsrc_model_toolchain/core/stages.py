"""Single-stage execution shared by the Blender operator and host CLIs."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .compatibility import compare_model_compatibility
from .errors import ToolchainError
from .mdl_v10 import compare_mdl_sequence_to_smd, inspect_mdl, validate_mdl_contract
from .model_contract import ContractError, load_contract, write_qc
from .paths import resolve_artifact_root, resolve_toolchain
from .smd import animation_budget_hint, audit_loop_endpoint, read_smd


PUBLIC_STAGES = ("PREFLIGHT", "EXPORT", "COMPILE", "INSPECT", "ROUNDTRIP")


def _apply_export_plan(contract: dict, root: Path) -> dict:
    """Apply export-time SMD body parts before QC generation and MDL inspection."""

    plan_path = (root / contract.get("outputs", {}).get("export_plan", "export_plan.json")).resolve()
    if root not in plan_path.parents or not plan_path.is_file():
        return contract
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolchainError("COMPILE", "compile.export_plan", f"invalid export plan: {exc}") from exc
    references = plan.get("references", [])
    by_source = {
        str(item.get("contract_source", "")).casefold(): item
        for item in references
        if isinstance(item, dict)
    }
    for body in contract.get("bodies", []):
        item = by_source.get(str(body.get("source", "")).casefold())
        if item is None:
            item = by_source.get(Path(str(body.get("source", ""))).name.casefold())
        sources = item.get("compiled_sources") if isinstance(item, dict) else None
        if not isinstance(sources, list) or not sources:
            continue
        normalized = []
        for source in sources:
            if not isinstance(source, str) or not source.lower().endswith(".smd"):
                raise ToolchainError("COMPILE", "compile.export_plan", "export plan contains an invalid SMD source")
            path = (root / source).resolve()
            if root not in path.parents or not path.is_file():
                raise ToolchainError("COMPILE", "compile.export_plan", "export plan references a missing SMD", {"source": source})
            normalized.append(source)
        body["_compiled_sources"] = normalized
    return contract


def _requirements(
    contract: dict,
    phase: str,
    summary: str,
    evidence: dict,
    *,
    status: str = "pass",
) -> list[dict]:
    evidence_status = status if status in {"pass", "fail"} else "fail"
    if evidence_status == "fail":
        summary = f"{summary}; stage did not pass"
    return [
        {"id": item["id"], "status": evidence_status, "summary": summary, "evidence": evidence}
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
        status=report.get("status", "fail"),
    )
    return report


def run_export(contract_path: str | Path, artifacts_dir: str | Path) -> dict:
    from ..blender.smd_export import export_contract

    return export_contract(contract_path, artifacts_dir)


def run_compile(contract_path: str | Path, artifacts_dir: str | Path) -> dict:
    root = Path(artifacts_dir).expanduser().resolve()
    contract = load_contract(contract_path, artifact_dir=root, require_files=True)
    contract = _apply_export_plan(contract, root)
    compiler = resolve_toolchain().sven_studiomdl
    if compiler is None or not compiler.is_file():
        raise ToolchainError("COMPILE", "compile.compiler", "Sven StudioMDL is missing")
    animation_documents = {
        sequence["name"]: read_smd(root / sequence["source"])
        for sequence in contract["sequences"]
    }
    budgets = {
        name: animation_budget_hint(document)
        for name, document in animation_documents.items()
    }
    loop_endpoints = {}
    for sequence in contract["sequences"]:
        if not sequence.get("loop"):
            continue
        audit = audit_loop_endpoint(animation_documents[sequence["name"]])
        loop_endpoints[sequence["name"]] = audit
        if audit["status"] != "pass":
            raise ToolchainError(
                "COMPILE", "compile.loop_endpoint",
                "Looped sequence must duplicate its first pose at the final SMD frame",
                {"sequence": sequence["name"], "audit": audit},
            )
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
        "animation_budget": budgets, "loop_endpoints": loop_endpoints,
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
    contract = _apply_export_plan(contract, root)
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
