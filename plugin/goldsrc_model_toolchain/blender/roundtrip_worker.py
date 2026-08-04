"""Background Blender entry point for an isolated ROUNDTRIP stage."""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path


def main() -> int:
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", required=True)
    parser.add_argument("--module-root", required=True)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--artifacts", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(arguments)
    module_root = str(Path(args.module_root).expanduser().resolve())
    if module_root not in sys.path:
        sys.path.insert(0, module_root)
    stages = importlib.import_module(args.package + ".core.stages")
    reporting = importlib.import_module(args.package + ".core.reporting")
    errors = importlib.import_module(args.package + ".core.errors")
    try:
        result = stages.execute_stage(
            "ROUNDTRIP", args.contract, args.artifacts,
            roundtrip_evidence_dir=args.evidence,
        )
        reporting.write_json(args.report, result)
        return 0
    except errors.ToolchainError as exc:
        reporting.write_json(args.report, reporting.failure_report(exc, stage="ROUNDTRIP"))
        return 1
    except Exception as exc:
        failure = errors.ToolchainError(
            "ROUNDTRIP", "roundtrip.worker", str(exc), {"type": type(exc).__name__},
        )
        reporting.write_json(args.report, reporting.failure_report(failure, stage="ROUNDTRIP"))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
