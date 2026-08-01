#!/usr/bin/env python3
"""Validate contracts, render QC, and inspect GoldSrc production artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from goldsrc_toolchain.mdl_v10 import inspect_mdl, patch_texture_flags
from goldsrc_toolchain.model_contract import ContractError, contract_summary, load_contract, write_qc
from goldsrc_toolchain.paths import ensure_outside_skill_tree, resolve_artifact_root


def _write_or_print(value: dict, output: Path | None) -> None:
    text = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    if output:
        ensure_outside_skill_tree(output, label="Report output").write_text(text, encoding="utf-8")
    else:
        print(text, end="")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("contract", type=Path)
    validate.add_argument("--artifacts", type=Path)
    validate.add_argument("--require-files", action="store_true")
    validate.add_argument("--output", type=Path)
    qc = commands.add_parser("qc")
    qc.add_argument("contract", type=Path)
    qc.add_argument("--artifacts", type=Path)
    inspect = commands.add_parser("inspect-mdl")
    inspect.add_argument("mdl", type=Path)
    inspect.add_argument("--output", type=Path)
    patch = commands.add_parser("patch-flags")
    patch.add_argument("mdl", type=Path)
    patch.add_argument("mapping", type=Path, help="JSON object mapping texture filenames to mode lists")
    patch.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "validate":
            root = resolve_artifact_root(args.artifacts or args.contract.parent)
            contract = load_contract(args.contract, artifact_dir=root, require_files=args.require_files)
            _write_or_print({"status": "pass", **contract_summary(contract)}, args.output)
        elif args.command == "qc":
            contract = load_contract(args.contract)
            root = resolve_artifact_root(args.artifacts or args.contract.parent)
            path = write_qc(contract, root)
            _write_or_print({"status": "pass", "qc": str(path)}, None)
        elif args.command == "inspect-mdl":
            _write_or_print(inspect_mdl(args.mdl), args.output)
        else:
            mapping = json.loads(args.mapping.read_text(encoding="utf-8"))
            destination = ensure_outside_skill_tree(args.output or args.mdl, label="Patched MDL")
            _write_or_print(patch_texture_flags(args.mdl, mapping, output=destination), None)
    except (ContractError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "fail", "issues": getattr(exc, "errors", [str(exc)])}, indent=2, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
