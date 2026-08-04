"""Shared release metadata helpers for the workspace scripts."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_plugin_manifest(root: Path = REPO_ROOT) -> dict:
    manifest_path = root / "plugin" / "goldsrc_model_toolchain" / "blender_manifest.toml"
    try:
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"cannot read Extension manifest: {manifest_path}: {exc}") from exc
    version = manifest.get("version")
    semver = (
        r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
        r"(?:-(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
        r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*)?"
        r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    )
    if not isinstance(version, str) or not re.fullmatch(semver, version):
        raise ValueError(f"Extension manifest has invalid version: {version!r}")
    return manifest


def plugin_version(root: Path = REPO_ROOT) -> str:
    return str(load_plugin_manifest(root)["version"])


def extension_archive_name(root: Path = REPO_ROOT, *, platform: str = "windows-x64") -> str:
    return f"goldsrc_model_toolchain-{plugin_version(root)}-{platform}.zip"
