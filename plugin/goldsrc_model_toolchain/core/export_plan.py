"""Build and apply export-time SMD and sparse-atlas decisions."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Iterable

from .errors import ToolchainError


EXPORT_PLAN_VERSION = 2


def _plan_error(phase: str, message: str, details: dict | None = None) -> ToolchainError:
    return ToolchainError(
        phase,
        f"{phase.casefold()}.export_plan",
        message,
        details or {},
    )


def build_export_plan(contract: dict, references: Iterable[dict]) -> dict:
    """Record compiled SMD parts and omit only unused generated atlas tiles."""

    reference_items = [dict(item) for item in references]
    declared = [
        str(texture["name"])
        for texture in contract.get("textures", [])
        if isinstance(texture, dict) and isinstance(texture.get("name"), str)
    ]
    canonical = {name.casefold(): name for name in declared}
    referenced_keys = {
        str(name).casefold()
        for family in contract.get("skin_families", [])
        if isinstance(family, list)
        for name in family
        if isinstance(name, str)
    }
    for item in reference_items:
        referenced_keys.update(
            str(name).casefold()
            for name in item.get("materials", [])
            if isinstance(name, str)
        )
        for report in item.get("large_texture_tiling", []):
            if not isinstance(report, dict):
                continue
            referenced_keys.update(
                str(name).casefold()
                for name in report.get("tiles", [])
                if isinstance(name, str)
            )

    unknown = sorted(key for key in referenced_keys if key not in canonical)
    if unknown:
        raise _plan_error(
            "EXPORT",
            "prepared SMD or skin families reference textures absent from the normalized contract",
            {"textures": unknown},
        )

    compiled = []
    omitted = []
    for texture in contract.get("textures", []):
        if not isinstance(texture, dict) or not isinstance(texture.get("name"), str):
            continue
        name = str(texture["name"])
        generated_tile = isinstance(texture.get("_large_texture"), dict)
        if generated_tile and name.casefold() not in referenced_keys:
            omitted.append(name)
        else:
            compiled.append(name)
    return {
        "version": EXPORT_PLAN_VERSION,
        "references": reference_items,
        "textures": {
            "declared": declared,
            "referenced": [canonical[key] for key in sorted(referenced_keys)],
            "compiled": compiled,
            "omitted_unused_large_tiles": omitted,
        },
    }


def _apply_reference_sources(contract: dict, references: list, root: Path, phase: str) -> None:
    by_source = {}
    for item in references:
        if not isinstance(item, dict):
            continue
        source = str(item.get("contract_source", "")).replace("\\", "/")
        by_source[source.casefold()] = item
        by_source.setdefault(Path(source).name.casefold(), item)
    for body in contract.get("bodies", []):
        source = str(body.get("source", "")).replace("\\", "/")
        item = by_source.get(source.casefold())
        if item is None:
            item = by_source.get(Path(source).name.casefold())
        sources = item.get("compiled_sources") if isinstance(item, dict) else None
        if not isinstance(sources, list) or not sources:
            continue
        normalized = []
        for compiled_source in sources:
            if not isinstance(compiled_source, str) or not compiled_source.lower().endswith(".smd"):
                raise _plan_error(phase, "export plan contains an invalid SMD source")
            path = (root / compiled_source).resolve()
            if root not in path.parents or not path.is_file():
                raise _plan_error(
                    phase,
                    "export plan references a missing SMD",
                    {"source": compiled_source},
                )
            normalized.append(compiled_source.replace("\\", "/"))
        body["_compiled_sources"] = normalized


def _apply_texture_selection(contract: dict, texture_plan: dict, phase: str) -> None:
    keys = ("declared", "compiled", "omitted_unused_large_tiles")
    if any(not isinstance(texture_plan.get(key), list) for key in keys):
        raise _plan_error(phase, "export plan texture selection is incomplete")
    if any(
        not isinstance(name, str)
        for key in keys
        for name in texture_plan[key]
    ):
        raise _plan_error(phase, "export plan texture selection contains a non-string name")

    textures = contract.get("textures", [])
    actual = {
        str(texture["name"]).casefold(): texture
        for texture in textures
        if isinstance(texture, dict) and isinstance(texture.get("name"), str)
    }
    declared = {name.casefold() for name in texture_plan["declared"]}
    compiled = {name.casefold() for name in texture_plan["compiled"]}
    omitted = {name.casefold() for name in texture_plan["omitted_unused_large_tiles"]}
    if len(declared) != len(texture_plan["declared"]) or len(compiled) != len(texture_plan["compiled"]) or len(omitted) != len(texture_plan["omitted_unused_large_tiles"]):
        raise _plan_error(phase, "export plan texture selection contains duplicate names")
    if declared != set(actual):
        raise _plan_error(
            phase,
            "export plan texture declarations do not match the contract",
            {"plan": sorted(declared), "contract": sorted(actual)},
        )
    if compiled & omitted or compiled | omitted != declared:
        raise _plan_error(phase, "export plan compiled and omitted textures do not partition declarations")

    invalid_omissions = sorted(
        name for name in omitted
        if not isinstance(actual[name].get("_large_texture"), dict)
    )
    referenced = texture_plan.get("referenced", [])
    if not isinstance(referenced, list) or any(not isinstance(name, str) for name in referenced):
        raise _plan_error(phase, "export plan referenced textures must be a string list")
    invalid_omissions.extend(sorted(omitted & {name.casefold() for name in referenced}))
    skin_references = {
        str(name).casefold()
        for family in contract.get("skin_families", [])
        if isinstance(family, list)
        for name in family
        if isinstance(name, str)
    }
    invalid_omissions.extend(sorted(omitted & skin_references))
    if invalid_omissions:
        raise _plan_error(
            phase,
            "export plan may omit only generated atlas tiles unused by geometry and skin families",
            {"textures": sorted(set(invalid_omissions))},
        )

    contract["textures"] = [
        texture for texture in textures
        if isinstance(texture, dict) and str(texture.get("name", "")).casefold() in compiled
    ]
    # The retained physical tiles are now authoritative; do not expand the atlas again.
    contract["large_textures"] = []


def apply_export_plan_data(contract: dict, plan: dict, root: Path, *, phase: str) -> dict:
    """Apply an already decoded plan to a copy of a normalized contract."""

    if not isinstance(plan, dict):
        raise _plan_error(phase, "export plan root must be an object")
    version = plan.get("version", 1)
    if not isinstance(version, int) or version < 1 or version > EXPORT_PLAN_VERSION:
        raise _plan_error(phase, "unsupported export plan version", {"version": version})
    references = plan.get("references", [])
    if not isinstance(references, list):
        raise _plan_error(phase, "export plan references must be a list")

    effective = copy.deepcopy(contract)
    _apply_reference_sources(effective, references, root, phase)
    if version >= 2:
        texture_plan = plan.get("textures")
        if not isinstance(texture_plan, dict):
            raise _plan_error(phase, "export plan texture selection is missing")
        _apply_texture_selection(effective, texture_plan, phase)
    return effective


def apply_export_plan(contract: dict, root: Path, *, phase: str) -> dict:
    """Load and apply the contract-owned plan when EXPORT has produced one."""

    plan_path = (root / contract.get("outputs", {}).get("export_plan", "export_plan.json")).resolve()
    if root not in plan_path.parents or not plan_path.is_file():
        return contract
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _plan_error(phase, f"invalid export plan: {exc}") from exc
    return apply_export_plan_data(contract, plan, root, phase=phase)
