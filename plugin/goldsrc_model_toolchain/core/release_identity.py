"""Derive runtime release identity from the Blender Extension manifest."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path


SEMVER = re.compile(
    r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)


def load_release_identity(manifest_path: Path) -> dict:
    path = Path(manifest_path).resolve()
    try:
        manifest = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError(f"cannot read Blender Extension manifest {path}: {exc}") from exc
    extension_id = manifest.get("id")
    version = manifest.get("version")
    if not isinstance(extension_id, str) or not extension_id:
        raise RuntimeError("Blender Extension manifest has no id")
    if not isinstance(version, str) or not SEMVER.fullmatch(version):
        raise RuntimeError(f"Blender Extension manifest has invalid version: {version!r}")
    release = None if "-" in version else f"v{version}"
    return {
        "id": extension_id,
        "version": version,
        "distribution": "development_build" if release is None else "public_github_release",
        "release": release,
        "public_compatibility_baseline": release,
    }
