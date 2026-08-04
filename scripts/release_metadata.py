"""Single-source release metadata helpers for workspace scripts."""

from __future__ import annotations

import json
import os
import re
import tempfile
import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_MANIFEST = Path("plugin/goldsrc_model_toolchain/blender_manifest.toml")
SKILL_RELEASE_PIN = Path("skill/build-goldsrc-models/scripts/toolchain-release.json")
REPOSITORY = "https://github.com/XiangXtreme/goldsrc-model-toolchain"
EXTENSION_ID = "goldsrc_model_toolchain"
DEFAULT_PLATFORM = "windows-x64"
SEMVER = re.compile(
    r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)


def validate_version(value: str, *, stable: bool = False) -> str:
    version = str(value)
    if not SEMVER.fullmatch(version):
        raise ValueError(f"invalid semantic version: {value!r}")
    if stable and ("-" in version or "+" in version):
        raise ValueError(f"public release version must be stable SemVer: {value!r}")
    return version


def load_plugin_manifest(root: Path = REPO_ROOT, manifest_path: Path | None = None) -> dict:
    manifest_path = manifest_path or root / PLUGIN_MANIFEST
    try:
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"cannot read Extension manifest: {manifest_path}: {exc}") from exc
    version = manifest.get("version")
    validate_version(version)
    return manifest


def plugin_version(root: Path = REPO_ROOT) -> str:
    return str(load_plugin_manifest(root)["version"])


def release_coordinates(
    version: str, *, repository: str = REPOSITORY, platform: str = DEFAULT_PLATFORM,
) -> dict:
    version = validate_version(version)
    tag = f"v{version}"
    asset = f"{EXTENSION_ID}-{version}-{platform}.zip"
    return {
        "version": version,
        "tag": tag,
        "asset": asset,
        "download_url": f"{repository.rstrip('/')}/releases/download/{tag}/{asset}",
    }


def extension_archive_name(root: Path = REPO_ROOT, *, platform: str = DEFAULT_PLATFORM) -> str:
    return release_coordinates(plugin_version(root), platform=platform)["asset"]


def load_skill_release(root: Path = REPO_ROOT, pin_path: Path | None = None) -> dict:
    path = pin_path or root / SKILL_RELEASE_PIN
    try:
        pin = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read Skill release pin: {path}: {exc}") from exc
    required = {
        "repository", "version", "sha256", "extension_id",
        "api_version", "blender", "platform",
    }
    missing = sorted(required - pin.keys()) if isinstance(pin, dict) else sorted(required)
    if not isinstance(pin, dict) or pin.get("schema_version") != 2 or missing:
        raise ValueError(f"invalid Skill release pin; missing={missing}")
    duplicated = sorted({"tag", "asset", "download_url"} & pin.keys())
    if duplicated:
        raise ValueError(f"derived release fields must not be stored: {duplicated}")
    if not re.fullmatch(r"[0-9a-f]{64}", str(pin.get("sha256", ""))):
        raise ValueError("Skill release SHA-256 must be 64 lowercase hex characters")
    coordinates = release_coordinates(
        pin["version"], repository=pin["repository"], platform=pin["platform"],
    )
    return {**pin, **coordinates}


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def set_plugin_version(version: str, root: Path = REPO_ROOT) -> Path:
    version = validate_version(version, stable=True)
    path = root / PLUGIN_MANIFEST
    text = path.read_bytes().decode("utf-8")
    pattern = re.compile(r'(?m)^version[ \t]*=[ \t]*"[^"\r\n]+"[ \t]*(?=\r?$)')
    updated, count = pattern.subn(f'version = "{version}"', text)
    if count != 1:
        raise ValueError(f"expected one version assignment in {path}, found {count}")
    atomic_write_bytes(path, updated.encode("utf-8"))
    return path


def write_skill_release_pin(
    version: str, sha256: str, root: Path = REPO_ROOT,
) -> Path:
    version = validate_version(version, stable=True)
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("release archive SHA-256 must be 64 lowercase hex characters")
    current = load_skill_release(root)
    pin = {
        "schema_version": 2,
        "repository": current["repository"],
        "version": version,
        "sha256": sha256,
        "extension_id": current["extension_id"],
        "api_version": current["api_version"],
        "blender": current["blender"],
        "platform": current["platform"],
    }
    path = root / SKILL_RELEASE_PIN
    atomic_write_bytes(
        path,
        (json.dumps(pin, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
    )
    return path
