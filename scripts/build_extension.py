#!/usr/bin/env python3
"""Validate and build the public Blender 5.2 GoldSrc Extension ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from goldsrc_toolchain.paths import ensure_outside_skill_tree, resolve_toolchain


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "extension" / "goldsrc_model_toolchain"


def build(output: Path, *, blender: Path | None = None) -> dict:
    executable = blender or resolve_toolchain().blender
    if executable is None or not executable.is_file():
        raise FileNotFoundError("Blender 5.2 executable was not resolved")
    output = ensure_outside_skill_tree(output, label="Extension archive")
    output.parent.mkdir(parents=True, exist_ok=True)
    validate = subprocess.run(
        [str(executable), "--command", "extension", "validate", "--valid-tags=", str(SOURCE)],
        capture_output=True, text=True, errors="replace", timeout=180,
    )
    if validate.returncode:
        raise RuntimeError(f"Extension validation failed:\n{validate.stdout}\n{validate.stderr}")
    built = subprocess.run(
        [
            str(executable), "--command", "extension", "build", "--source-dir", str(SOURCE),
            "--output-filepath", str(output), "--valid-tags=",
        ],
        capture_output=True, text=True, errors="replace", timeout=300,
    )
    if built.returncode or not output.is_file():
        raise RuntimeError(f"Extension build failed:\n{built.stdout}\n{built.stderr}")
    return {
        "status": "pass", "source": str(SOURCE), "archive": str(output),
        "bytes": output.stat().st_size,
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "validate_stdout": validate.stdout[-4000:],
        "build_stdout": built.stdout[-4000:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--blender", type=Path)
    args = parser.parse_args()
    report = build(args.output, blender=args.blender)
    checksum = Path(report["archive"] + ".sha256")
    checksum.write_text(f"{report['sha256']}  {Path(report['archive']).name}\n", encoding="ascii")
    report["checksum"] = str(checksum)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
