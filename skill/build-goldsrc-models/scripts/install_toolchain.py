#!/usr/bin/env python3
"""Inspect or install the pinned public GoldSrc Blender Extension release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path


MANIFEST_PATH = Path(__file__).with_name("toolchain-release.json")
STATE_MARKER = "GOLDSRC_TOOLCHAIN_STATE="


def load_release() -> dict:
    value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    required = {
        "repository", "tag", "asset", "download_url", "sha256",
        "extension_id", "version", "api_version", "blender", "platform",
    }
    missing = sorted(required - value.keys())
    if value.get("schema_version") != 1 or missing:
        raise ValueError(f"invalid toolchain release manifest; missing={missing}")
    if not re.fullmatch(r"[0-9a-f]{64}", value["sha256"]):
        raise ValueError("toolchain release SHA-256 must be 64 lowercase hex characters")
    return value


def _blender_candidates(explicit: Path | None) -> list[Path]:
    values: list[Path] = []
    if explicit is not None:
        values.append(explicit)
    if os.environ.get("GOLDSRC_BLENDER"):
        values.append(Path(os.environ["GOLDSRC_BLENDER"]))
    for name in ("ProgramW6432", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        if os.environ.get(name):
            values.append(Path(os.environ[name]) / "Blender Foundation" / "Blender 5.2" / "blender.exe")
    if os.name == "nt":
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            values.append(Path(f"{letter}:\\Program Files\\Blender Foundation\\Blender 5.2\\blender.exe"))
    unique: list[Path] = []
    seen: set[str] = set()
    for value in values:
        key = str(value.expanduser()).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(value.expanduser())
    return unique


def resolve_blender(explicit: Path | None = None) -> Path:
    blender = next((path.resolve() for path in _blender_candidates(explicit) if path.is_file()), None)
    if blender is None:
        raise FileNotFoundError("Blender 5.2 was not found; pass --blender or set GOLDSRC_BLENDER")
    completed = subprocess.run(
        [str(blender), "--version"], capture_output=True, text=True, errors="replace", timeout=30,
    )
    first = (completed.stdout or completed.stderr).splitlines()[0] if completed.stdout or completed.stderr else ""
    if completed.returncode or not re.search(r"Blender\s+5\.2(?:\.|\s|$)", first):
        raise RuntimeError(f"expected Blender 5.2.x, got: {first or blender}")
    return blender


def _query_installed(blender: Path) -> dict:
    expression = (
        "import bpy,json;"
        "a=bpy.app.driver_namespace.get('goldsrc_model_toolchain');"
        "c=a.capabilities() if a else None;"
        f"print({STATE_MARKER!r}+json.dumps({{'capabilities':c,'enabled':sorted(bpy.context.preferences.addons.keys())}}))"
    )
    completed = subprocess.run(
        [str(blender), "--background", "--python-expr", expression],
        capture_output=True, text=True, errors="replace", timeout=120,
    )
    match = re.search(rf"^{re.escape(STATE_MARKER)}(.+)$", completed.stdout, re.MULTILINE)
    if completed.returncode or not match:
        raise RuntimeError(f"could not inspect installed Extension:\n{completed.stdout}\n{completed.stderr}")
    return json.loads(match.group(1))


def _version_tuple(value: str) -> tuple[int, int, int, int]:
    match = re.fullmatch(
        r"(\d+)\.(\d+)\.(\d+)"
        r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
        r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?",
        str(value),
    )
    if not match:
        return (-1, -1, -1, -1)
    major, minor, patch, prerelease = match.groups()
    return int(major), int(minor), int(patch), 0 if prerelease else 1


def version_compatibility(capabilities: dict | None, release: dict) -> str:
    if not capabilities or capabilities.get("id") != release["extension_id"]:
        return "missing"
    if capabilities.get("api_version") != release["api_version"]:
        return "incompatible_api"
    installed = _version_tuple(capabilities.get("version", ""))
    validated = _version_tuple(release["version"])
    if installed == validated:
        return "validated"
    if installed > validated:
        return "compatible_unregressed_version"
    return "upgrade_required"


def _validate_archive(path: Path, release: dict) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != release["sha256"]:
        raise ValueError(f"Extension archive SHA-256 mismatch: expected {release['sha256']}, got {digest}")
    return digest


def _download(release: dict, destination: Path) -> None:
    request = urllib.request.Request(
        release["download_url"], headers={"User-Agent": "build-goldsrc-models-skill/1"},
    )
    with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)


def _install(blender: Path, archive: Path) -> dict:
    completed = subprocess.run(
        [str(blender), "--command", "extension", "install-file", "-r", "user_default", "-e", str(archive)],
        capture_output=True, text=True, errors="replace", timeout=300,
    )
    if completed.returncode:
        raise RuntimeError(f"Extension install failed:\n{completed.stdout}\n{completed.stderr}")
    return {"returncode": completed.returncode, "stdout": completed.stdout[-4000:], "stderr": completed.stderr[-4000:]}


def run(*, blender_path: Path | None = None, apply: bool = False) -> dict:
    release = load_release()
    blender = resolve_blender(blender_path)
    before = _query_installed(blender)
    compatibility = version_compatibility(before.get("capabilities"), release)
    install_report = None
    if apply and compatibility not in {"validated", "compatible_unregressed_version"}:
        with tempfile.TemporaryDirectory(prefix="goldsrc-toolchain-release-") as temporary:
            archive = Path(temporary) / release["asset"]
            _download(release, archive)
            _validate_archive(archive, release)
            install_report = _install(blender, archive)
    after = _query_installed(blender)
    final_compatibility = version_compatibility(after.get("capabilities"), release)
    status = "pass" if final_compatibility in {"validated", "compatible_unregressed_version"} else "needs_action"
    return {
        "status": status,
        "blender": str(blender),
        "release": release,
        "before": {"compatibility": compatibility, "capabilities": before.get("capabilities")},
        "installed": install_report,
        "after": {"compatibility": final_compatibility, "capabilities": after.get("capabilities")},
        "blender_mcp_modified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Install the pinned release when missing or older")
    parser.add_argument("--blender", type=Path)
    args = parser.parse_args()
    try:
        report = run(blender_path=args.blender, apply=args.apply)
    except Exception as exc:
        report = {
            "status": "fail",
            "error": {"type": type(exc).__name__, "message": str(exc)},
            "blender_mcp_modified": False,
        }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
