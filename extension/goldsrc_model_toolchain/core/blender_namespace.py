"""Scoped Blender datablock cleanup for repeatable long-lived MCP authoring."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any


SUPPORTED_DATABLOCKS = (
    "objects",
    "meshes",
    "curves",
    "armatures",
    "materials",
    "images",
    "actions",
)


def _normalize_names(names: Mapping[str, Iterable[str]]) -> dict[str, tuple[str, ...]]:
    unknown = sorted(set(names) - set(SUPPORTED_DATABLOCKS))
    if unknown:
        raise ValueError(f"unsupported Blender datablock collections: {', '.join(unknown)}")
    normalized: dict[str, tuple[str, ...]] = {}
    for kind, values in names.items():
        clean = tuple(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
        if clean:
            normalized[kind] = clean
    if not normalized:
        raise ValueError("at least one owned Blender datablock name is required")
    return normalized


def _matches(name: str, base: str) -> bool:
    return name == base or re.fullmatch(re.escape(base) + r"\.\d{3,}", name) is not None


def inspect_asset_namespace(
    names: Mapping[str, Iterable[str]],
    *,
    bpy_module: Any | None = None,
) -> dict[str, Any]:
    if bpy_module is None:
        import bpy as bpy_module
    owned = _normalize_names(names)
    facts: dict[str, dict[str, dict[str, Any]]] = {}
    for kind, bases in owned.items():
        data = getattr(bpy_module.data, kind)
        facts[kind] = {}
        for base in bases:
            matches = sorted(item.name for item in data if _matches(item.name, base))
            facts[kind][base] = {
                "exact": base in matches,
                "suffixes": [name for name in matches if name != base],
                "matches": matches,
            }
    return {"status": "pass", "datablocks": facts}


def purge_asset_namespace(
    names: Mapping[str, Iterable[str]],
    *,
    bpy_module: Any | None = None,
) -> dict[str, Any]:
    """Remove only explicitly owned names and their Blender numeric suffixes."""

    if bpy_module is None:
        import bpy as bpy_module
    owned = _normalize_names(names)
    object_bases = owned.get("objects", ())
    objects = [
        obj
        for obj in bpy_module.data.objects
        if any(_matches(obj.name, base) for base in object_bases)
    ]
    physics_owned = [
        obj.name
        for obj in objects
        if getattr(obj, "rigid_body", None) is not None
        or getattr(obj, "rigid_body_constraint", None) is not None
    ]
    if physics_owned:
        raise RuntimeError(
            "scoped namespace contains Bullet-owned objects; tear down constraints, rigid bodies, "
            f"and the rigid-body world first: {', '.join(sorted(physics_owned))}"
        )

    removed: dict[str, list[str]] = {}
    for kind in SUPPORTED_DATABLOCKS:
        bases = owned.get(kind, ())
        if not bases:
            continue
        data = getattr(bpy_module.data, kind)
        matches = [item for item in list(data) if any(_matches(item.name, base) for base in bases)]
        removed[kind] = sorted(item.name for item in matches)
        for item in matches:
            if hasattr(item, "use_fake_user"):
                item.use_fake_user = False
            data.remove(item, do_unlink=True)
    return {"status": "pass", "removed": removed}


def assert_exact_asset_namespace(
    names: Mapping[str, Iterable[str]],
    *,
    bpy_module: Any | None = None,
) -> dict[str, Any]:
    report = inspect_asset_namespace(names, bpy_module=bpy_module)
    issues = []
    for kind, bases in report["datablocks"].items():
        for base, facts in bases.items():
            if not facts["exact"]:
                issues.append(f"{kind}.{base} is missing")
            if facts["suffixes"]:
                issues.append(f"{kind}.{base} has suffixed collisions: {', '.join(facts['suffixes'])}")
    if issues:
        raise RuntimeError("asset namespace is not exact: " + "; ".join(issues))
    return report
