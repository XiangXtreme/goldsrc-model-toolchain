"""Compatibility entry point for contract-driven Extension SMD export."""

from __future__ import annotations

import json
import os
from pathlib import Path

import bpy


def main() -> dict:
    contract = os.environ.get("GOLDSRC_MODEL_CONTRACT")
    artifacts = os.environ.get("GOLDSRC_ARTIFACTS") or bpy.context.scene.get("goldsrc_output_dir")
    if not contract or not artifacts:
        raise RuntimeError("GOLDSRC_MODEL_CONTRACT and GOLDSRC_ARTIFACTS are required")
    api = bpy.app.driver_namespace.get("goldsrc_model_toolchain")
    if api is None:
        raise RuntimeError("goldsrc_model_toolchain Extension runtime API is not registered")
    report = api.execute_stage("EXPORT", contract, artifacts)
    output = Path(artifacts).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / "blender_stage.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print("BLENDER_STAGE", json.dumps(report, sort_keys=True, default=str))
    return report


if __name__ == "__main__":
    main()
