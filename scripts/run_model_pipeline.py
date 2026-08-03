#!/usr/bin/env python3
"""Run a content-hashed Blender-MCP/host GoldSrc model production pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import runpy
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from blender_mcp_client import request
from goldsrc_toolchain.model_contract import DEFAULT_PHASES, ContractError, load_contract
from goldsrc_toolchain.paths import resolve_artifact_root


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLCHAIN_INPUTS = (
    Path(__file__).resolve().parent / "goldsrc_toolchain",
    REPO_ROOT / "plugin" / "goldsrc_model_toolchain",
    REPO_ROOT / "tool-manifest.json",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _expand(value: str, *, artifacts: Path, spec_dir: Path) -> str:
    return value.format(artifacts=str(artifacts), repo=str(REPO_ROOT), spec_dir=str(spec_dir))


def _resolve(value: str, *, artifacts: Path, spec_dir: Path, base: Path) -> Path:
    path = Path(_expand(value, artifacts=artifacts, spec_dir=spec_dir)).expanduser()
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def _hash_path(path: Path) -> dict[str, Any]:
    if path.is_file():
        return {"path": str(path), "kind": "file", "sha256": _sha256(path)}
    if path.is_dir():
        files = [
            {"path": child.relative_to(path).as_posix(), "sha256": _sha256(child)}
            for child in sorted(item for item in path.rglob("*") if item.is_file() and "__pycache__" not in item.parts)
        ]
        return {"path": str(path), "kind": "directory", "files": files}
    return {"path": str(path), "kind": "missing"}


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _fingerprint(stage: dict[str, Any], script: Path, inputs: list[Path], dependency: str, contract_path: Path | None) -> str:
    is_environment = stage.get("phase") == "environment"
    return _digest({
        "runner": _hash_path(Path(__file__).resolve()),
        "toolchain": None if is_environment else [_hash_path(path) for path in TOOLCHAIN_INPUTS],
        "contract": _hash_path(contract_path) if contract_path and not is_environment else None,
        "stage": stage,
        "script": _hash_path(script),
        "inputs": [_hash_path(path) for path in inputs],
        "dependency": dependency,
    })


def _write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _load_cache(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 2, "stages": {}}
    return value if value.get("version") == 2 and isinstance(value.get("stages"), dict) else {"version": 2, "stages": {}}


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _clean_outputs(paths: list[Path], artifacts: Path) -> None:
    for path in paths:
        if not _inside(path, artifacts):
            raise ValueError(f"stage output must stay inside artifacts: {path}")
        if path.is_file():
            path.unlink()
        elif path.exists():
            raise ValueError(f"pipeline outputs must be files: {path}")


def _run_blender(
    script: Path,
    environment: dict[str, str],
    host: str,
    port: int,
    timeout: float,
    blend_checkpoint: Path | None = None,
) -> dict[str, Any]:
    checkpoint_response = None
    if blend_checkpoint is not None:
        checkpoint_code = "\n".join([
            "import bpy",
            f"_result = bpy.ops.wm.open_mainfile(filepath={str(blend_checkpoint)!r})",
            "if 'FINISHED' not in _result:",
            "    raise RuntimeError(f'open_mainfile returned {_result}')",
            "print(bpy.data.filepath)",
        ])
        checkpoint_response = request(host, port, {"type": "execute_code", "params": {"code": checkpoint_code}}, timeout)
        if checkpoint_response.get("status") != "success":
            raise RuntimeError(f"Blender MCP checkpoint load failed: {checkpoint_response}")
    code = "\n".join([
        "import importlib, os, runpy, sys",
        f"os.environ.update({environment!r})",
        "importlib.invalidate_caches()",
        "for _name in tuple(sys.modules):",
        "    if _name == 'goldsrc_toolchain' or _name.startswith('goldsrc_toolchain.') or _name == 'fixtures' or _name.startswith('fixtures.'):",
        "        sys.modules.pop(_name, None)",
        f"runpy.run_path({str(script)!r}, run_name='__main__')",
    ])
    response = request(host, port, {"type": "execute_code", "params": {"code": code}}, timeout)
    if response.get("status") != "success":
        raise RuntimeError(f"Blender MCP stage failed: {response}")
    return {"checkpoint": checkpoint_response, "stage": response} if checkpoint_response else response


def _run_host(runner: str, script: Path, arguments: list[str], environment: dict[str, str], timeout: float) -> dict[str, Any]:
    if runner == "runpy":
        previous = os.environ.copy()
        try:
            os.environ.update(environment)
            result = runpy.run_path(str(script), run_name="__main__")
        finally:
            os.environ.clear()
            os.environ.update(previous)
        return {"result_keys": sorted(result)}
    command = [sys.executable, str(script), *arguments]
    process_environment = os.environ.copy()
    process_environment.update(environment)
    completed = subprocess.run(command, capture_output=True, text=True, errors="replace", env=process_environment, timeout=timeout)
    if completed.returncode != 0:
        raise RuntimeError(f"host stage failed with exit code {completed.returncode}\n{completed.stdout}\n{completed.stderr}")
    return {"command": command, "returncode": completed.returncode, "stdout": completed.stdout[-12000:], "stderr": completed.stderr[-12000:]}


def _run_reuse_report(source_report: Path, outputs: list[Path], artifacts: Path) -> dict[str, Any]:
    """Reuse a passing upstream report without pretending to rerun its stage."""
    if not _inside(source_report, artifacts):
        raise ValueError(f"reuse_report source must stay inside artifacts: {source_report}")
    if len(outputs) != 1:
        raise ValueError("reuse_report stages must declare exactly one JSON output")
    try:
        value = json.loads(source_report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"reuse_report source is not valid JSON: {source_report}: {exc}") from exc
    if value.get("status") not in {"pass", "pass_with_known_blockers"}:
        raise ValueError(f"reuse_report source is not passing: {source_report}")
    outputs[0].write_bytes(source_report.read_bytes())
    return {"runner": "reuse_report", "source_report": str(source_report)}


def _has_evidence(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return value is not None


def _validate_stage_result(stage: dict[str, Any], outputs: list[Path]) -> tuple[list[dict], list[dict], list[dict]]:
    issues: list[dict] = []
    blockers: list[dict] = []
    requirement_evidence: list[dict] = []
    result_name = stage.get("result_json")
    if not result_name:
        return issues, blockers, requirement_evidence
    matches = [path for path in outputs if path.name == Path(result_name).name or path.as_posix().endswith(str(result_name).replace("\\", "/"))]
    if len(matches) != 1:
        raise ValueError(f"stage {stage['name']} result_json must identify one declared output")
    value = json.loads(matches[0].read_text(encoding="utf-8"))
    status = value.get("status")
    if status not in {"pass", "pass_with_known_blockers"}:
        raise RuntimeError(f"stage {stage['name']} result report is not passing: {status}")
    issues.extend(value.get("issues", []))
    blockers.extend(value.get("known_blockers", []))
    if status == "pass_with_known_blockers" and not blockers:
        raise RuntimeError(f"stage {stage['name']} claims known blockers without listing them")
    raw_evidence = value.get("requirement_evidence", [])
    if not isinstance(raw_evidence, list):
        raise RuntimeError(f"stage {stage['name']} requirement_evidence must be a list")
    for index, item in enumerate(raw_evidence):
        if not isinstance(item, dict):
            raise RuntimeError(f"stage {stage['name']} requirement_evidence[{index}] must be an object")
        requirement_id = item.get("id")
        evidence_status = item.get("status")
        summary = item.get("summary")
        if not isinstance(requirement_id, str) or not requirement_id.strip():
            raise RuntimeError(f"stage {stage['name']} requirement_evidence[{index}].id is required")
        if evidence_status not in {"pass", "fail"}:
            raise RuntimeError(f"stage {stage['name']} requirement_evidence[{index}].status must be pass or fail")
        if not isinstance(summary, str) or not summary.strip():
            raise RuntimeError(f"stage {stage['name']} requirement_evidence[{index}].summary is required")
        if not _has_evidence(item.get("evidence")):
            raise RuntimeError(f"stage {stage['name']} requirement_evidence[{index}].evidence is required")
        requirement_evidence.append({**item, "stage": stage["name"], "phase": stage.get("phase")})
    return issues, blockers, requirement_evidence


def _evaluate_requirement_evidence(contract: dict[str, Any] | None, evidence: list[dict]) -> tuple[list[dict], dict[str, dict]]:
    if not contract or contract.get("version") == 1:
        return [], {}
    requirements = contract.get("intent", {}).get("requirements", [])
    expected = {item["id"].casefold(): item for item in requirements}
    issues: list[dict] = []
    grouped: dict[str, list[dict]] = {key: [] for key in expected}
    for item in evidence:
        key = str(item.get("id", "")).casefold()
        if key not in expected:
            issues.append({
                "severity": "error",
                "code": "requirements.unknown",
                "message": f"stage evidence references unknown requirement: {item.get('id')}",
                "context": {"stage": item.get("stage"), "phase": item.get("phase")},
            })
            continue
        grouped[key].append(item)
    results: dict[str, dict] = {}
    for key, requirement in expected.items():
        entries = grouped[key]
        missing_phases: list[str] = []
        failed_phases: list[str] = []
        for phase in requirement["evidence_phases"]:
            phase_entries = [item for item in entries if item.get("phase") == phase]
            if not phase_entries:
                missing_phases.append(phase)
            elif any(item.get("status") != "pass" for item in phase_entries):
                failed_phases.append(phase)
        status = "pass" if not missing_phases and not failed_phases else "fail"
        results[requirement["id"]] = {
            "status": status,
            "source": requirement["source"],
            "required_phases": requirement["evidence_phases"],
            "missing_phases": missing_phases,
            "failed_phases": failed_phases,
            "evidence": entries,
        }
        if status == "fail":
            issues.append({
                "severity": "error",
                "code": "requirements.unproven",
                "message": f"explicit requirement is not fully proven: {requirement['id']}",
                "context": {"missing_phases": missing_phases, "failed_phases": failed_phases},
            })
    return issues, results


def _claims(completed_phases: set[str], *, failed: bool) -> dict[str, bool]:
    return {
        "blender_equivalent_reproduced": not failed and {"author", "preflight", "export"}.issubset(completed_phases),
        "original_tool_reproduced": False,
        "sven_compiled": not failed and "compile_sven" in completed_phases,
        "mdl_v10_inspected": not failed and "mdl_inspect" in completed_phases,
        "sourceio_geometry_roundtrip": not failed and "sourceio_roundtrip" in completed_phases,
        "visual_reviewed": not failed and "visual_review" in completed_phases,
        "in_game_validated": False,
    }


def main(*, cache_name: str = ".goldsrc-model-pipeline.json", report_name: str | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9876)
    parser.add_argument("--force-stage", action="append", default=[])
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()
    spec_path = args.spec.expanduser().resolve()
    spec_dir = spec_path.parent
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    artifacts_value = spec.get("artifacts")
    if not isinstance(artifacts_value, str):
        parser.error("spec.artifacts must be a path string")
    try:
        artifacts = resolve_artifact_root(
            _resolve(artifacts_value, artifacts=spec_dir, spec_dir=spec_dir, base=spec_dir)
        )
    except ValueError as exc:
        parser.error(str(exc))
    artifacts.mkdir(parents=True, exist_ok=True)

    contract_path = None
    contract = None
    if spec.get("contract") is not None:
        if not isinstance(spec["contract"], str):
            parser.error("spec.contract must be a path string")
        contract_path = _resolve(spec["contract"], artifacts=artifacts, spec_dir=spec_dir, base=spec_dir)
        contract = load_contract(contract_path, artifact_dir=artifacts, require_files=False)
    stages = spec.get("stages")
    if not isinstance(stages, list) or not stages:
        parser.error("spec.stages must be a non-empty list")
    names = [stage.get("name") for stage in stages]
    if any(not isinstance(name, str) or not name for name in names) or len(names) != len(set(names)):
        parser.error("stage names must be non-empty and unique")
    if contract:
        phases = [stage.get("phase") for stage in stages]
        required = contract["acceptance"]["required_phases"]
        missing = [phase for phase in required if phase not in phases]
        positions = [DEFAULT_PHASES.index(phase) for phase in phases if phase in DEFAULT_PHASES]
        if missing or positions != sorted(positions):
            parser.error(f"contract pipeline phases are missing or out of order; missing={missing}")

    cache_path = artifacts / cache_name
    cache = _load_cache(cache_path)
    common_environment = {key: _expand(str(value), artifacts=artifacts, spec_dir=spec_dir) for key, value in spec.get("environment", {}).items()}
    dependency = _digest({
        "shared_environment": common_environment,
        "mcp_host": args.host,
        "mcp_port": args.port,
    })
    report_stages: dict[str, dict[str, Any]] = {}
    all_issues: list[dict] = []
    blockers: list[dict] = []
    requirement_evidence: list[dict] = []
    requirement_results: dict[str, dict] = {}
    completed_phases: set[str] = set()
    started_pipeline = time.perf_counter()
    error = None

    try:
        for stage_index, stage in enumerate(stages):
            name = stage["name"]
            runner = stage.get("runner")
            if runner not in {"blender_mcp", "python", "runpy", "reuse_report"}:
                raise ValueError(f"unsupported runner for {name}: {runner}")
            if runner == "reuse_report":
                source_value = stage.get("source_report")
                if not isinstance(source_value, str):
                    raise ValueError(f"stage {name} reuse_report requires source_report")
                script = _resolve(source_value, artifacts=artifacts, spec_dir=spec_dir, base=artifacts)
                if not script.is_file():
                    raise FileNotFoundError(f"reuse_report source not found: {script}")
            else:
                script = _resolve(stage["script"], artifacts=artifacts, spec_dir=spec_dir, base=spec_dir)
                if not script.is_file():
                    raise FileNotFoundError(f"stage script not found: {script}")
            inputs = [_resolve(value, artifacts=artifacts, spec_dir=spec_dir, base=spec_dir) for value in stage.get("inputs", [])]
            outputs = [_resolve(value, artifacts=artifacts, spec_dir=spec_dir, base=artifacts) for value in stage.get("outputs", [])]
            if not outputs:
                raise ValueError(f"stage {name} must declare at least one output")
            if any(not _inside(path, artifacts) for path in outputs):
                raise ValueError(f"stage {name} output escapes artifacts")
            environment = dict(common_environment)
            environment.update({key: _expand(str(value), artifacts=artifacts, spec_dir=spec_dir) for key, value in stage.get("environment", {}).items()})
            arguments = [_expand(str(value), artifacts=artifacts, spec_dir=spec_dir) for value in stage.get("args", [])]
            timeout = float(stage.get("timeout_seconds", 300.0))
            blend_checkpoint = None
            if stage.get("blend_checkpoint") is not None:
                if runner != "blender_mcp" or not isinstance(stage["blend_checkpoint"], str):
                    raise ValueError(f"stage {name} blend_checkpoint requires a path string and blender_mcp runner")
                blend_checkpoint = _resolve(stage["blend_checkpoint"], artifacts=artifacts, spec_dir=spec_dir, base=spec_dir)
                if not blend_checkpoint.is_file():
                    raise FileNotFoundError(f"stage {name} checkpoint not found: {blend_checkpoint}")
            fingerprint = _fingerprint(stage, script, inputs, dependency, contract_path)
            cached = cache["stages"].get(name, {})
            outputs_exist = all(path.is_file() for path in outputs)
            current_result_fingerprint = _digest([_hash_path(path) for path in outputs]) if outputs_exist else None
            cache_hit = (
                not args.no_cache
                and name not in args.force_stage
                and cached.get("status") == "pass"
                and cached.get("fingerprint") == fingerprint
                and cached.get("result_fingerprint") == current_result_fingerprint
            )
            started = time.perf_counter()
            if cache_hit:
                state = "cached"
                result_fingerprint = cached["result_fingerprint"]
            else:
                for later in stages[stage_index:]:
                    cache["stages"].pop(later["name"], None)
                _clean_outputs(outputs, artifacts)
                cache["stages"][name] = {"status": "running", "fingerprint": fingerprint}
                _write_json(cache_path, cache)
                if runner == "reuse_report":
                    result = _run_reuse_report(script, outputs, artifacts)
                    state = "reused"
                else:
                    result = _run_blender(script, environment, args.host, args.port, timeout, blend_checkpoint) if runner == "blender_mcp" else _run_host(runner, script, arguments, environment, timeout)
                    state = "executed"
                missing = [str(path) for path in outputs if not path.is_file()]
                if missing:
                    raise RuntimeError(f"stage {name} did not create declared outputs: {missing}")
                output_facts = [_hash_path(path) for path in outputs]
                result_fingerprint = _digest(output_facts)
                elapsed = round(time.perf_counter() - started, 3)
            if stage.get("phase") == "export":
                if contract_path is None:
                    raise RuntimeError("export phase requires a model contract")
                contract = load_contract(contract_path, artifact_dir=artifacts, require_files=True)
            if not cache_hit:
                cache["stages"][name] = {"status": "pass", "fingerprint": fingerprint, "result_fingerprint": result_fingerprint, "duration_seconds": elapsed, "outputs": output_facts}
                _write_json(cache_path, cache)
                _write_json(artifacts / f"pipeline_{name}.log", result)
            stage_issues, stage_blockers, stage_requirement_evidence = _validate_stage_result(stage, outputs)
            all_issues.extend(stage_issues)
            blockers.extend(stage_blockers)
            requirement_evidence.extend(stage_requirement_evidence)
            elapsed = round(time.perf_counter() - started, 3) if state == "executed" else 0.0
            report_stages[name] = {"status": state, "phase": stage.get("phase"), "duration_seconds": elapsed, "outputs": [str(path) for path in outputs]}
            if stage.get("phase"):
                completed_phases.add(stage["phase"])
            dependency = result_fingerprint
        requirement_issues, requirement_results = _evaluate_requirement_evidence(contract, requirement_evidence)
        all_issues.extend(requirement_issues)
        status = "fail" if requirement_issues else "pass_with_known_blockers" if blockers else "pass"
    except Exception as exc:
        status = "fail"
        error = str(exc)
        all_issues.append({"severity": "error", "code": "pipeline.exception", "message": error, "context": {}})

    blockers = list({item.get("code", _digest(item)): item for item in blockers}.values())
    report = {
        "status": status,
        "contract_version": contract["version"] if contract else 1,
        "target_profile": contract["target_profile"] if contract else spec.get("target_profile", "half-life-cs"),
        "duration_seconds": round(time.perf_counter() - started_pipeline, 3),
        "artifacts": str(artifacts),
        "issues": all_issues,
        "known_blockers": blockers,
        "stages": report_stages,
        "requirements": requirement_results,
        "revision": contract.get("intent", {}).get("revision") if contract else None,
        "claims": _claims(completed_phases, failed=status == "fail"),
        "error": error,
    }
    chosen_report = report_name or (contract["outputs"]["report"] if contract else "model_pipeline_report.json")
    _write_json(artifacts / chosen_report, report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if status in {"pass", "pass_with_known_blockers"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
