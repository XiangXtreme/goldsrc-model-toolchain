"""Compatibility entry point for the Extension-owned independent MDL readback."""

from __future__ import annotations

import json
import os
from pathlib import Path

import bpy


def run_roundtrip(contract_path: Path | str, artifacts: Path | str) -> dict:
    api = bpy.app.driver_namespace.get("goldsrc_model_toolchain")
    if api is None:
        raise RuntimeError("goldsrc_model_toolchain Extension runtime API is not registered")
    report = api.execute_stage("ROUNDTRIP", str(contract_path), str(artifacts))
    output = Path(artifacts).expanduser().resolve() / "roundtrip_stage.json"
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    contract = os.environ.get("GOLDSRC_MODEL_CONTRACT")
    artifacts = os.environ.get("GOLDSRC_ARTIFACTS")
    if not contract or not artifacts:
        raise SystemExit("GOLDSRC_MODEL_CONTRACT and GOLDSRC_ARTIFACTS are required")
    print(json.dumps(run_roundtrip(contract, artifacts), indent=2, ensure_ascii=False))
