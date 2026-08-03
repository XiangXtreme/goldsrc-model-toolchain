#!/usr/bin/env python3
"""Synchronize the local Skill and Blender Extension installations."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_MANIFEST = REPO_ROOT / "workspace-manifest.json"
SKILL_EXCLUDED_PARTS = {".git", ".claude", "__pycache__", ".pytest_cache", ".mypy_cache"}
SKILL_EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def _load_workspace() -> dict:
    return json.loads(WORKSPACE_MANIFEST.read_text(encoding="utf-8"))


def _skill_destination(explicit: Path | None) -> Path:
    if explicit:
        return explicit.expanduser().resolve()
    root = Path(os.environ.get("GOLDSRC_CODEX_SKILLS", Path.home() / ".codex" / "skills"))
    return (root.expanduser() / "build-goldsrc-models").resolve()


def _source_files(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*")
        if path.is_file()
        and not (set(path.parts) & SKILL_EXCLUDED_PARTS)
        and path.suffix.lower() not in SKILL_EXCLUDED_SUFFIXES
    )


def sync_skill(source: Path, destination: Path, *, dry_run: bool) -> dict:
    source = source.resolve()
    destination = destination.resolve()
    if source == destination:
        raise ValueError("Skill source and destination must be different")
    if not source.is_dir() or not (source / "SKILL.md").is_file():
        raise FileNotFoundError(f"invalid Skill source: {source}")
    copied = []
    for path in _source_files(source):
        relative = path.relative_to(source)
        target = destination / relative
        copied.append(relative.as_posix())
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    return {"status": "dry-run" if dry_run else "pass", "source": str(source), "destination": str(destination), "files": copied}


def _resolve_blender(explicit: Path | None) -> Path:
    candidates = []
    if explicit:
        candidates.append(explicit.expanduser())
    if os.environ.get("GOLDSRC_BLENDER"):
        candidates.append(Path(os.environ["GOLDSRC_BLENDER"]))
    from goldsrc_toolchain.paths import resolve_toolchain

    resolved = resolve_toolchain().blender
    if resolved:
        candidates.append(resolved)
    for candidate in candidates:
        path = Path(candidate).resolve()
        if path.is_file():
            return path
    raise FileNotFoundError("Blender 5.2 executable was not resolved; pass --blender or set GOLDSRC_BLENDER")


def sync_plugin(source: Path, *, blender: Path | None, dry_run: bool) -> dict:
    source = source.resolve()
    manifest = source / "blender_manifest.toml"
    if not source.is_dir() or not manifest.is_file():
        raise FileNotFoundError(f"invalid Extension source: {source}")
    if dry_run:
        return {"status": "dry-run", "source": str(source), "install_repository": "user_default"}

    executable = _resolve_blender(blender)
    from build_extension import build

    with tempfile.TemporaryDirectory(prefix="goldsrc-suite-sync-") as temporary:
        archive = Path(temporary) / "goldsrc_model_toolchain-local.zip"
        build_report = build(archive, blender=executable)
        completed = subprocess.run(
            [str(executable), "--command", "extension", "install-file", "-r", "user_default", "-e", str(archive)],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=300,
        )
        if completed.returncode:
            raise RuntimeError(f"Extension install failed:\n{completed.stdout}\n{completed.stderr}")
    return {
        "status": "pass",
        "source": str(source),
        "blender": str(executable),
        "install_repository": "user_default",
        "build": build_report,
        "install_stdout": completed.stdout[-4000:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--skill", action="store_true", help="synchronize only the Skill")
    selection.add_argument("--plugin", action="store_true", help="build and install only the Extension")
    selection.add_argument("--all", action="store_true", help="synchronize both components")
    parser.add_argument("--dry-run", action="store_true", help="show planned file/install operations")
    parser.add_argument("--skill-destination", type=Path)
    parser.add_argument("--blender", type=Path)
    args = parser.parse_args()

    workspace = _load_workspace()
    components = workspace["components"]
    skill_source = (REPO_ROOT / components["skill"]["source"]).resolve()
    plugin_source = (REPO_ROOT / components["plugin"]["source"]).resolve()
    run_skill = args.skill or args.all or not (args.skill or args.plugin or args.all)
    run_plugin = args.plugin or args.all or not (args.skill or args.plugin or args.all)
    report = {"status": "pass", "workspace": str(REPO_ROOT), "components": {}}
    if run_skill:
        report["components"]["skill"] = sync_skill(
            skill_source,
            _skill_destination(args.skill_destination),
            dry_run=args.dry_run,
        )
    if run_plugin:
        report["components"]["plugin"] = sync_plugin(
            plugin_source,
            blender=args.blender,
            dry_run=args.dry_run,
        )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
