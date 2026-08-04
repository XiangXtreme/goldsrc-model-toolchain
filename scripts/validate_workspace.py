#!/usr/bin/env python3
"""Validate the combined Skill and Blender Extension workspace."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from release_metadata import load_plugin_manifest, load_skill_release


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_MANIFEST = Path("workspace-manifest.json")


def _read_json(path: Path, errors: list[str]) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"cannot read {path.as_posix()}: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"manifest must be an object: {path.as_posix()}")
        return {}
    return value


def _source_path(root: Path, value: str, label: str, errors: list[str]) -> Path:
    path = (root / value).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        errors.append(f"{label} escapes workspace: {value}")
    return path


def validate(root: Path = REPO_ROOT) -> dict:
    root = root.resolve()
    errors: list[str] = []
    workspace = _read_json(root / WORKSPACE_MANIFEST, errors)
    components = workspace.get("components", {})
    skill = components.get("skill", {})
    plugin = components.get("plugin", {})
    skill_repository = skill.get("repository")
    plugin_repository = plugin.get("repository")
    if skill_repository != plugin_repository:
        errors.append("Skill and Plugin must use the same monorepo repository")
    if plugin_repository != "https://github.com/XiangXtreme/goldsrc-model-toolchain":
        errors.append("workspace repository must be the public goldsrc-model-toolchain monorepo")

    skill_root = _source_path(root, skill.get("source", ""), "Skill source", errors)
    plugin_root = _source_path(root, plugin.get("source", ""), "Plugin source", errors)
    skill_manifest = skill_root / "SKILL.md"
    plugin_manifest = plugin_root / "blender_manifest.toml"

    skill_manifests = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("SKILL.md")
        if path.is_file()
    )
    if skill_manifests != ["skill/build-goldsrc-models/SKILL.md"]:
        errors.append(f"expected one workspace Skill manifest, found: {skill_manifests}")
    if not skill_manifest.is_file():
        errors.append(f"missing Skill manifest: {skill_manifest.relative_to(root).as_posix()}")
    for path in skill_root.rglob("*") if skill_root.is_dir() else []:
        if path.is_dir() and path.name in {".git", ".claude"}:
            errors.append(f"local metadata is not part of Skill source: {path.relative_to(root).as_posix()}")

    plugin_data: dict = {}
    if not plugin_manifest.is_file():
        errors.append(f"missing plugin manifest: {plugin_manifest.relative_to(root).as_posix()}")
    else:
        try:
            plugin_data = load_plugin_manifest(root, plugin_manifest)
        except ValueError as exc:
            errors.append(str(exc))

    plugin_id = plugin_data.get("id")
    plugin_version = plugin_data.get("version")
    if plugin_id != plugin.get("id"):
        errors.append(f"plugin id mismatch: manifest={plugin_id!r}, workspace={plugin.get('id')!r}")
    if plugin_data.get("blender_version_min", "").split(".")[:2] != ["5", "2"]:
        errors.append("plugin minimum Blender version is not 5.2.x")

    tool_manifest = _read_json(root / "tool-manifest.json", errors)
    bundle = tool_manifest.get("bundles", {}).get(plugin.get("id"), {})
    if bundle.get("root") != plugin.get("source"):
        errors.append(f"tool manifest plugin root mismatch: {bundle.get('root')!r}")
    if bundle.get("version_source") != "blender_manifest.toml":
        errors.append("tool manifest must derive the plugin version from blender_manifest.toml")

    release_path = skill_root / "scripts" / "toolchain-release.json"
    release = {}
    if not release_path.is_file():
        errors.append(f"missing Skill release manifest: {release_path.relative_to(root).as_posix()}")
    else:
        try:
            release = load_skill_release(root, release_path)
        except ValueError as exc:
            errors.append(str(exc))
    if release.get("version") != plugin_version:
        errors.append(
            f"Skill release version mismatch: pin={release.get('version')!r}, plugin={plugin_version!r}"
        )
    if release.get("extension_id") != plugin_id:
        errors.append("Skill release Extension id does not match the plugin manifest")
    if release.get("repository") != plugin_repository:
        errors.append("Skill release repository does not match the workspace repository")
    if release.get("api_version") != plugin.get("api_version"):
        errors.append("Skill release API does not match the workspace plugin API")

    return {
        "status": "pass" if not errors else "fail",
        "workspace": workspace.get("workspace"),
        "skill": {"source": skill_root.relative_to(root).as_posix(), "manifest": skill_manifests},
        "plugin": {
            "source": plugin_root.relative_to(root).as_posix(),
            "id": plugin_id,
            "version": plugin_version,
            "api_version": plugin.get("api_version"),
        },
        "release": {
            "version": release.get("version"),
            "tag": release.get("tag"),
            "asset": release.get("asset"),
        },
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args()
    report = validate(args.root)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
