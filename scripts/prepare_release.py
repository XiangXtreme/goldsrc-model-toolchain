#!/usr/bin/env python3
"""Atomically stamp, build, and pin one public Extension release."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from build_extension import build, write_checksum
from release_metadata import (
    PLUGIN_MANIFEST,
    REPO_ROOT,
    SKILL_RELEASE_PIN,
    atomic_write_bytes,
    release_coordinates,
    set_plugin_version,
    validate_version,
    write_skill_release_pin,
)
from validate_workspace import validate


def _outside_workspace(path: Path, root: Path) -> Path:
    resolved = path.expanduser().resolve()
    workspace = root.resolve()
    if resolved == workspace or workspace in resolved.parents:
        raise ValueError(f"release output directory must be outside the workspace: {resolved}")
    return resolved


def prepare_release(
    version: str,
    output_dir: Path,
    *,
    root: Path = REPO_ROOT,
    blender: Path | None = None,
    dry_run: bool = False,
    build_func=build,
    validate_func=validate,
) -> dict:
    version = validate_version(version, stable=True)
    root = root.resolve()
    output_dir = _outside_workspace(output_dir, root)
    coordinates = release_coordinates(version)
    archive = output_dir / coordinates["asset"]
    checksum = Path(str(archive) + ".sha256")
    changed = [PLUGIN_MANIFEST.as_posix(), SKILL_RELEASE_PIN.as_posix()]
    if dry_run:
        return {
            "status": "dry-run",
            **coordinates,
            "archive": str(archive),
            "checksum": str(checksum),
            "source_files": changed,
        }
    existing = [str(path) for path in (archive, checksum) if path.exists()]
    if existing:
        raise FileExistsError(f"release output already exists: {existing}")

    manifest_path = root / PLUGIN_MANIFEST
    pin_path = root / SKILL_RELEASE_PIN
    originals = {
        manifest_path: manifest_path.read_bytes(),
        pin_path: pin_path.read_bytes(),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    created = [archive, checksum]
    try:
        set_plugin_version(version, root)
        report = build_func(archive, blender=blender)
        if report.get("status") != "pass" or report.get("version") != version:
            raise RuntimeError(
                f"Extension build identity mismatch: requested={version!r}, report={report!r}"
            )
        checksum_path = write_checksum(report)
        if checksum_path != checksum:
            raise RuntimeError(f"unexpected checksum path: {checksum_path}")
        write_skill_release_pin(version, report["sha256"], root)
        workspace = validate_func(root)
        if workspace.get("status") != "pass":
            raise RuntimeError(f"workspace validation failed after release preparation: {workspace}")
    except Exception:
        for path, data in originals.items():
            atomic_write_bytes(path, data)
        for path in reversed(created):
            if path.is_file():
                path.unlink()
        raise

    return {
        "status": "pass",
        **coordinates,
        "archive": str(archive),
        "checksum": str(checksum),
        "bytes": report["bytes"],
        "sha256": report["sha256"],
        "source_files": changed,
        "workspace_validation": workspace["status"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="Stable semantic version, for example 2.0.0")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--blender", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        report = prepare_release(
            args.version,
            args.output_dir,
            blender=args.blender,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        report = {
            "status": "fail",
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["status"] in {"pass", "dry-run"} else 1


if __name__ == "__main__":
    sys.exit(main())
