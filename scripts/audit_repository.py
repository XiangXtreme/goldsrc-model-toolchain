#!/usr/bin/env python3
"""Audit the public GoldSrc toolchain repository without writing artifacts."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = Path("skill/build-goldsrc-models")
SKILL_MANIFEST = SKILL_ROOT / "SKILL.md"
EXTENSION_ROOT = Path("plugin/goldsrc_model_toolchain")
REQUIRED_FILES = {
    Path("LICENSE"),
    Path("README.md"),
    Path("tool-manifest.json"),
    Path("workspace-manifest.json"),
    SKILL_MANIFEST,
    EXTENSION_ROOT / "blender_manifest.toml",
    EXTENSION_ROOT / "api.py",
    EXTENSION_ROOT / "operator.py",
    EXTENSION_ROOT / "core/stages.py",
    EXTENSION_ROOT / "bin/windows-x64/studiomdl.exe",
    EXTENSION_ROOT / "wheels/pillow-12.3.0-cp313-cp313-win_amd64.whl",
}
FORBIDDEN_RUNTIME = {"artifacts", "dist", "outputs", "work"}
FORBIDDEN_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache"}
FORBIDDEN_EXTENSION_PARTS = {"source1", "source2", "bsp", "dmx", "vta", "vtf", "vmt", "vpk"}
FORBIDDEN_EXTENSION_TEXT = (
    "io_scene_valvesource", "import SourceIO", "from SourceIO.",
    "import blender_mcp", "from blender_mcp", "bpy.types.Panel", "bpy.types.Menu",
)
TEXT_SUFFIXES = {".json", ".md", ".py", ".txt", ".toml", ".yaml", ".yml"}
WINDOWS_ABSOLUTE = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]")


def _payload_files(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*")
        if path.is_file()
        and ".git" not in path.parts
        and not (set(path.parts) & FORBIDDEN_PARTS)
        and path.suffix.lower() not in {".pyc", ".pyo"}
    )


def _canonical_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    if b"\0" in data:
        return data
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    return data.replace(b"\r\n", b"\n")


def tree_digest(root: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    files = _payload_files(root)
    total = 0
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        data = _canonical_bytes(path)
        digest.update(len(relative).to_bytes(4, "little"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "little"))
        digest.update(data)
        total += len(data)
    return digest.hexdigest(), len(files), total


def audit(root: Path = REPO_ROOT) -> dict:
    root = root.resolve()
    files = _payload_files(root)
    relative_files = {path.relative_to(root) for path in files}
    errors: list[str] = []
    for name in sorted(FORBIDDEN_RUNTIME):
        if (root / name).exists():
            errors.append(f"runtime directory must stay outside repository: {name}/")
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if path.is_symlink():
            errors.append(f"symlink is not portable: {relative.as_posix()}")
        if set(relative.parts) & FORBIDDEN_PARTS or path.suffix.lower() in {".pyc", ".pyo"}:
            errors.append(f"generated cache is forbidden: {relative.as_posix()}")
        if path.is_file() and path.name == "SKILL.md" and relative != SKILL_MANIFEST:
            errors.append(f"unexpected Skill manifest: {relative.as_posix()}")
    skill_manifests = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("SKILL.md")
        if path.is_file()
    )
    if skill_manifests != [SKILL_MANIFEST.as_posix()]:
        errors.append(
            "workspace must contain exactly one Skill manifest at "
            f"{SKILL_MANIFEST.as_posix()}: {skill_manifests}"
        )
    for required in sorted(REQUIRED_FILES):
        if required not in relative_files:
            errors.append(f"missing required file: {required.as_posix()}")

    extension = root / EXTENSION_ROOT
    for path in _payload_files(extension):
        relative = path.relative_to(extension)
        forbidden = {part.casefold() for part in relative.parts} & FORBIDDEN_EXTENSION_PARTS
        if forbidden:
            errors.append(f"non-GoldSrc Extension path: {relative.as_posix()}")
        if path.suffix.lower() == ".py":
            text = path.read_text(encoding="utf-8")
            for token in FORBIDDEN_EXTENSION_TEXT:
                if token in text:
                    errors.append(f"forbidden Extension token {token!r}: {relative.as_posix()}")

    for path in files:
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8")
        if WINDOWS_ABSOLUTE.search(text):
            errors.append(f"machine-specific absolute path: {path.relative_to(root).as_posix()}")
        if path.suffix.lower() == ".py":
            try:
                ast.parse(text, filename=str(path))
            except SyntaxError as exc:
                errors.append(f"Python syntax error in {path.relative_to(root).as_posix()}: {exc}")

    try:
        manifest = json.loads((root / "tool-manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        manifest = {}
        errors.append(f"cannot read tool manifest: {exc}")
    bundle = manifest.get("bundles", {}).get("goldsrc_model_toolchain", {})
    extension_manifest_path = root / EXTENSION_ROOT / "blender_manifest.toml"
    try:
        extension_manifest = tomllib.loads(extension_manifest_path.read_text(encoding="utf-8"))
        extension_version = extension_manifest.get("version")
    except (OSError, tomllib.TOMLDecodeError) as exc:
        extension_version = None
        errors.append(f"cannot read Extension manifest: {exc}")
    digest, count, byte_count = tree_digest(extension) if extension.is_dir() else ("", 0, 0)
    if manifest.get("distribution") != "public_github_release":
        errors.append("manifest distribution must be public_github_release")
    if bundle.get("root") != EXTENSION_ROOT.as_posix():
        errors.append("manifest bundle path does not match the Extension source")
    if bundle.get("version") != extension_version:
        errors.append(
            f"manifest bundle version {bundle.get('version')!r} does not match Extension {extension_version!r}"
        )
    if (bundle.get("sha256_tree"), bundle.get("files"), bundle.get("bytes")) != (digest, count, byte_count):
        errors.append("manifest Extension tree integrity does not match repository contents")
    mcp = manifest.get("external_tools", {}).get("blender_mcp", {})
    if mcp.get("ownership") != "external" or mcp.get("managed_by_extension") is not False:
        errors.append("official Blender MCP must remain external and unmanaged")
    return {
        "status": "pass" if not errors else "fail",
        "repository": "goldsrc-model-toolchain",
        "files": len(files),
        "skill": {"root": SKILL_ROOT.as_posix(), "manifests": skill_manifests},
        "extension": {"files": count, "bytes": byte_count, "sha256_tree": digest},
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args()
    report = audit(args.root)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
