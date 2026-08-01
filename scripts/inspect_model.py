#!/usr/bin/env python3
"""Compatibility CLI for the Extension-owned INSPECT stage."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from goldsrc_toolchain.errors import ToolchainError
from goldsrc_toolchain.paths import ensure_outside_skill_tree, resolve_artifact_root
from goldsrc_toolchain.stages import execute_stage


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contract", type=Path)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    contract = args.contract.expanduser().resolve()
    try:
        artifacts = resolve_artifact_root(args.artifacts or contract.parent)
        output = ensure_outside_skill_tree(
            args.output or artifacts / "mdl_inspection.json", label="Inspection report",
        )
    except ValueError as exc:
        parser.error(str(exc))
    try:
        report = execute_stage("INSPECT", contract, artifacts)
    except ToolchainError as exc:
        report = {
            "status": "fail",
            "phase": "mdl_inspect",
            "error": exc.as_dict(),
            "issues": [exc.as_dict()],
            "known_blockers": [],
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": report.get("status"), "issues": report.get("issues", [])}, indent=2, ensure_ascii=False))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
