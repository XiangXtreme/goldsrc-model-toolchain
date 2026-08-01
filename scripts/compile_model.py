#!/usr/bin/env python3
"""Compatibility CLI for the Extension-owned COMPILE stage."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from goldsrc_toolchain.errors import ToolchainError
from goldsrc_toolchain.paths import ensure_outside_skill_tree, resolve_artifact_root
from goldsrc_toolchain.stages import execute_stage


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contract", type=Path)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--compiler", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    contract = args.contract.expanduser().resolve()
    try:
        artifacts = resolve_artifact_root(args.artifacts or contract.parent)
        report_path = ensure_outside_skill_tree(
            args.report or artifacts / "compile_sven.json", label="Compile report",
        )
    except ValueError as exc:
        parser.error(str(exc))
    previous = os.environ.get("GOLDSRC_SVEN_STUDIOMDL")
    try:
        if args.compiler:
            os.environ["GOLDSRC_SVEN_STUDIOMDL"] = str(args.compiler.expanduser().resolve())
        report = execute_stage("COMPILE", contract, artifacts)
    except ToolchainError as exc:
        report = {
            "status": "fail",
            "phase": "compile_sven",
            "error": exc.as_dict(),
            "issues": [exc.as_dict()],
        }
    finally:
        if args.compiler:
            if previous is None:
                os.environ.pop("GOLDSRC_SVEN_STUDIOMDL", None)
            else:
                os.environ["GOLDSRC_SVEN_STUDIOMDL"] = previous
    _write(report_path, report)
    print(json.dumps({key: report.get(key) for key in ("status", "compiler", "mdl", "issues")}, indent=2, ensure_ascii=False))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
