"""Versioned production contract for Blender-to-GoldSrc model builds."""

from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable

from .large_textures import (
    GOLDSRC_MAX_TEXTURES_PER_MODEL,
    LargeTextureError,
    tile_name,
    validate_large_texture_spec,
)
from .material_mapping import STATIC_MATERIAL_AUDIT_FIELD
from .smd import SmdError, geometry_budget, read_smd, validate_smd
from .textures import TextureError, validate_indexed_bmp
from .physics_events import normalize_physics, validate_physics_definition


CONTRACT_VERSION = 2
SUPPORTED_CONTRACT_VERSIONS = {1, 2}
TARGET_PROFILES = {"half-life-cs", "sven-coop"}
TEXTURE_MODES = {"flatshade", "chrome", "fullbright", "nomips", "alpha", "additive", "masked"}
MOTION_AXES = {"X", "Y", "Z", "XR", "YR", "ZR", "LX", "LY", "LZ", "AX", "AY", "AZ", "AXR", "AYR", "AZR"}
CONTROLLER_AXES = {"X", "Y", "Z", "XR", "YR", "ZR", "M"}
DEFAULT_PHASES = [
    "environment", "author", "preflight", "export", "compile_sven", "mdl_inspect",
    "sourceio_roundtrip", "visual_review",
]
REQUIREMENT_EVIDENCE_PHASES = set(DEFAULT_PHASES) - {"environment"}
_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


def _qc_text(value: Any, label: str, errors: list[str], *, allow_empty: bool = False) -> str | None:
    """Validate text that will be emitted inside a quoted QC token."""

    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        errors.append(f"{label} must be a non-empty string")
        return None
    if '"' in value or any(ord(character) < 32 or ord(character) == 127 for character in value):
        errors.append(f"{label} must not contain quotes or control characters")
    return value


def _expand_large_textures(contract: dict[str, Any]) -> None:
    """Materialize 512px tile records while retaining the logical atlas declaration."""

    atlases = contract.get("large_textures", [])
    textures = list(contract.get("textures", []))
    names = {
        item.get("name", "").casefold(): item
        for item in textures
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    collisions = []
    if not isinstance(atlases, list):
        return
    for atlas in atlases:
        if not isinstance(atlas, dict):
            continue
        try:
            normalized = validate_large_texture_spec(atlas)
        except LargeTextureError:
            continue
        name = str(normalized["name"])
        image = str(normalized["image"])
        width = int(normalized["width"])
        height = int(normalized["height"])
        tile_size = int(normalized["tile_size"])
        for tile_y in range(height // tile_size):
            for tile_x in range(width // tile_size):
                tile = tile_name(name, tile_x, tile_y)
                large_texture = {
                    "name": name,
                    "image": image,
                    "width": width,
                    "height": height,
                    "tile_size": tile_size,
                    "tile_x": tile_x,
                    "tile_y": tile_y,
                }
                if tile.casefold() in names:
                    existing = names[tile.casefold()]
                    if existing.get("_large_texture") != large_texture:
                        collisions.append(
                            f"large texture tile name {tile} conflicts with declared texture {existing.get('name', tile)}"
                        )
                    continue
                textures.append({
                    "name": tile,
                    "source": tile,
                    "width": tile_size,
                    "height": tile_size,
                    "modes": list(atlas.get("modes", [])),
                    "alpha_threshold": atlas.get("alpha_threshold", 128),
                    "require_masked_pixels": atlas.get("require_masked_pixels", True),
                    "_large_texture": large_texture,
                })
                names[tile.casefold()] = textures[-1]
    if collisions:
        raise ContractError(collisions)
    contract["textures"] = textures


class ContractError(ValueError):
    """Raised with all deterministic contract errors, not only the first one."""

    def __init__(self, errors: Iterable[str]):
        self.errors = list(dict.fromkeys(errors))
        super().__init__("invalid model contract:\n- " + "\n- ".join(self.errors))


def effective_texture_modes(texture: dict[str, Any]) -> list[str]:
    """Return explicit modes plus deterministic legacy filename-set flags."""

    modes = list(texture.get("modes", []))
    name = Path(str(texture.get("name", ""))).name.casefold()
    if name.startswith("chrome_"):
        for mode in ("chrome", "flatshade"):
            if mode not in modes:
                modes.append(mode)
    return modes


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _is_vec3(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 3 and all(_is_number(item) for item in value)


def _safe_relative(value: Any, label: str, errors: list[str], *, suffix: str | None = None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label} must be a non-empty relative path")
        return None
    _qc_text(value, label, errors)
    value = value.replace("\\", "/")
    windows = PureWindowsPath(value)
    if windows.is_absolute() or windows.drive or value.startswith("/") or ".." in Path(value).parts:
        errors.append(f"{label} must stay inside the artifact directory: {value}")
    if suffix and Path(value).suffix.casefold() != suffix:
        errors.append(f"{label} must end with {suffix}: {value}")
    return value


def _unique_names(items: Any, label: str, errors: list[str]) -> set[str]:
    if not isinstance(items, list):
        errors.append(f"{label} must be a list")
        return set()
    seen: set[str] = set()
    for index, item in enumerate(items):
        name = item.get("name") if isinstance(item, dict) else None
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{label}[{index}].name is required")
            continue
        _qc_text(name, f"{label}[{index}].name", errors)
        key = name.casefold()
        if key in seen:
            errors.append(f"duplicate {label} name: {name}")
        seen.add(key)
    return seen


def _nonempty_strings(value: Any, label: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list) or not value:
        errors.append(f"{label} must be a non-empty list")
        return []
    strings: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{label}[{index}] must be a non-empty string")
            continue
        strings.append(item.strip())
    if len(strings) != len(set(strings)):
        errors.append(f"{label} must not contain duplicates")
    return strings


def _validate_intent(contract: dict[str, Any], errors: list[str]) -> None:
    if contract.get("version") == 1:
        return
    intent = contract.get("intent")
    if not isinstance(intent, dict):
        errors.append("version 2 contract requires intent")
        return
    request = intent.get("request")
    request_text = request if isinstance(request, str) else ""
    if not request_text.strip():
        errors.append("intent.request must preserve the non-empty user request")
    requirements = intent.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        errors.append("intent.requirements must be a non-empty list")
        requirements = []
    requirement_ids: set[str] = set()
    for index, requirement in enumerate(requirements):
        label = f"intent.requirements[{index}]"
        if not isinstance(requirement, dict):
            errors.append(f"{label} must be an object")
            continue
        requirement_id = requirement.get("id")
        if not isinstance(requirement_id, str) or not _NAME.match(requirement_id):
            errors.append(f"{label}.id must use letters, digits, dot, underscore, or dash")
        elif requirement_id.casefold() in requirement_ids:
            errors.append(f"duplicate intent requirement id: {requirement_id}")
        else:
            requirement_ids.add(requirement_id.casefold())
        source = requirement.get("source")
        if not isinstance(source, str) or not source.strip():
            errors.append(f"{label}.source must quote one explicit user requirement")
        elif source not in request_text:
            errors.append(f"{label}.source must appear verbatim in intent.request")
        phases = _nonempty_strings(requirement.get("evidence_phases"), f"{label}.evidence_phases", errors)
        if any(phase not in REQUIREMENT_EVIDENCE_PHASES for phase in phases):
            errors.append(f"{label}.evidence_phases contains an unsupported content-evidence phase")
        required_phases = contract.get("acceptance", {}).get("required_phases", [])
        if isinstance(required_phases, list) and any(phase not in required_phases for phase in phases):
            errors.append(f"{label}.evidence_phases must be included in acceptance.required_phases")
    assumptions = intent.setdefault("assumptions", [])
    if not isinstance(assumptions, list):
        errors.append("intent.assumptions must be a list")
    else:
        assumption_ids: set[str] = set()
        for index, assumption in enumerate(assumptions):
            label = f"intent.assumptions[{index}]"
            if not isinstance(assumption, dict):
                errors.append(f"{label} must be an object")
                continue
            assumption_id = assumption.get("id")
            if not isinstance(assumption_id, str) or not _NAME.match(assumption_id):
                errors.append(f"{label}.id must use letters, digits, dot, underscore, or dash")
            elif assumption_id.casefold() in assumption_ids:
                errors.append(f"duplicate intent assumption id: {assumption_id}")
            else:
                assumption_ids.add(assumption_id.casefold())
            for field in ("statement", "reason"):
                if not isinstance(assumption.get(field), str) or not assumption[field].strip():
                    errors.append(f"{label}.{field} must be a non-empty string")
    revision = intent.get("revision")
    if revision is None:
        return
    if not isinstance(revision, dict):
        errors.append("intent.revision must be an object")
        return
    _safe_relative(revision.get("baseline_report"), "intent.revision.baseline_report", errors, suffix=".json")
    _nonempty_strings(revision.get("changed_factors"), "intent.revision.changed_factors", errors)
    _nonempty_strings(revision.get("preserve"), "intent.revision.preserve", errors)


def _validate_texture_bake(contract: dict[str, Any], errors: list[str]) -> None:
    spec = contract.get("texture_bake")
    if spec is None:
        return
    if not isinstance(spec, dict):
        errors.append("texture_bake must be an object")
        return
    unknown = sorted(set(spec) - {"uv_layer", "require_active_render"})
    if unknown:
        errors.append(f"unsupported texture_bake fields: {', '.join(unknown)}")
    uv_layer = spec.get("uv_layer")
    if not isinstance(uv_layer, str) or not uv_layer.strip():
        errors.append("texture_bake.uv_layer must be a non-empty UV layer name")
    require_active_render = spec.get("require_active_render", True)
    if not isinstance(require_active_render, bool):
        errors.append("texture_bake.require_active_render must be a boolean")


def _validate_static_material_audit(contract: dict[str, Any], errors: list[str]) -> None:
    audit = contract.get(STATIC_MATERIAL_AUDIT_FIELD)
    if audit is None:
        return
    if not isinstance(audit, dict):
        errors.append(f"{STATIC_MATERIAL_AUDIT_FIELD} must be an object")
        return
    schema_version = audit.get("schema_version")
    if schema_version not in {1, 2}:
        errors.append(f"{STATIC_MATERIAL_AUDIT_FIELD}.schema_version must be 1 or 2")
    if audit.get("status") != "pass":
        errors.append(f"{STATIC_MATERIAL_AUDIT_FIELD}.status must be pass")
    for field in ("source_object", "prepared_object"):
        if not isinstance(audit.get(field), str) or not audit[field].strip():
            errors.append(f"{STATIC_MATERIAL_AUDIT_FIELD}.{field} must be a non-empty object name")
    for surface_name in ("source_evaluated", "prepared"):
        surface = audit.get(surface_name)
        if not isinstance(surface, dict):
            errors.append(f"{STATIC_MATERIAL_AUDIT_FIELD}.{surface_name} must be an object")
            continue
        materials = surface.get("materials")
        if not isinstance(materials, list) or not materials:
            errors.append(f"{STATIC_MATERIAL_AUDIT_FIELD}.{surface_name}.materials must be non-empty")
            continue
        for index, material in enumerate(materials):
            label = f"{STATIC_MATERIAL_AUDIT_FIELD}.{surface_name}.materials[{index}]"
            if not isinstance(material, dict):
                errors.append(f"{label} must be an object")
                continue
            for count in ("slot", "faces", "triangles"):
                if not isinstance(material.get(count), int) or material[count] < 0:
                    errors.append(f"{label}.{count} must be a non-negative integer")
            if surface_name == "prepared" and (
                not isinstance(material.get("token"), str) or not material["token"].strip()
            ):
                errors.append(f"{label}.token must be a logical texture token")
        if schema_version == 2:
            for field in ("geometry_sha256", "material_assignment_sha256"):
                value = surface.get(field)
                if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                    errors.append(
                        f"{STATIC_MATERIAL_AUDIT_FIELD}.{surface_name}.{field} must be a lowercase SHA-256"
                    )
    mapping = audit.get("old_to_new")
    if not isinstance(mapping, list) or not mapping:
        errors.append(f"{STATIC_MATERIAL_AUDIT_FIELD}.old_to_new must be non-empty")


def normalize_contract(value: dict[str, Any]) -> dict[str, Any]:
    contract = copy.deepcopy(value)
    contract.setdefault("version", CONTRACT_VERSION)
    contract.setdefault("target_profile", "half-life-cs")
    contract.setdefault("scale", 1.0)
    for key in ("bones", "bone_renames", "bodies", "bodygroups", "textures", "large_textures", "skin_families", "sequences", "hitboxes", "attachments", "controllers"):
        contract.setdefault(key, [])
    _expand_large_textures(contract)
    model_name = contract.get("model_name", "model.mdl")
    stem = Path(str(model_name)).stem or "model"
    outputs = contract.setdefault("outputs", {})
    outputs.setdefault("qc", f"{stem}.qc")
    outputs.setdefault("sven_mdl", str(model_name))
    outputs.setdefault("report", "model_pipeline_report.json")
    outputs.setdefault("export_plan", "export_plan.json")
    acceptance = contract.setdefault("acceptance", {})
    acceptance.setdefault("required_phases", list(DEFAULT_PHASES))
    acceptance.setdefault("visual_views", ["front", "three_quarter", "side"])
    acceptance.setdefault("allow_known_blockers", [])
    limitations = contract.setdefault("limitations", {})
    if isinstance(limitations, dict):
        limitations.setdefault("external_sequence_groups", [])
    texture_bake = contract.get("texture_bake")
    if isinstance(texture_bake, dict):
        texture_bake.setdefault("require_active_render", True)
    if "physics" in contract and isinstance(contract["physics"], dict):
        contract["physics"] = normalize_physics(contract["physics"])
    return contract


def validate_contract(
    value: dict[str, Any],
    *,
    artifact_dir: Path | str | None = None,
    require_files: bool = False,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(["contract root must be an object"])
    contract = normalize_contract(value)
    errors: list[str] = []
    if contract["version"] not in SUPPORTED_CONTRACT_VERSIONS:
        errors.append(f"unsupported contract version: {contract['version']}")
    if contract["target_profile"] not in TARGET_PROFILES:
        errors.append(f"unsupported target_profile: {contract['target_profile']}")
    model_name = _safe_relative(contract.get("model_name"), "model_name", errors, suffix=".mdl")
    if model_name and not _NAME.match(Path(model_name).name):
        errors.append("model_name filename may only contain letters, digits, dot, underscore, and dash")
    if not _is_number(contract.get("scale")) or float(contract["scale"]) <= 0:
        errors.append("scale must be a finite positive number")
    limitations = contract.get("limitations")
    if not isinstance(limitations, dict):
        errors.append("limitations must be an object")
    else:
        unknown_limitations = sorted(set(limitations) - {"external_sequence_groups"})
        if unknown_limitations:
            errors.append(f"unsupported limitations: {', '.join(unknown_limitations)}")
        external_groups = limitations.get("external_sequence_groups")
        if not isinstance(external_groups, list):
            errors.append("limitations.external_sequence_groups must be a list")
        else:
            names = []
            for index, name in enumerate(external_groups):
                if not isinstance(name, str) or not name.strip():
                    errors.append(f"limitations.external_sequence_groups[{index}] must be a sequence name")
                else:
                    names.append(name.casefold())
            if len(names) != len(set(names)):
                errors.append("limitations.external_sequence_groups must not contain duplicates")
    _validate_intent(contract, errors)
    _validate_texture_bake(contract, errors)
    _validate_static_material_audit(contract, errors)
    errors.extend(validate_physics_definition(contract.get("physics")))

    bones = contract["bones"]
    bone_names = _unique_names(bones, "bones", errors)
    if len(bones) > 128:
        errors.append("GoldSrc bone budget exceeded: at most 128 bones are exportable")
    bone_by_name = {item.get("name", "").casefold(): item for item in bones if isinstance(item, dict)}
    for index, bone in enumerate(bones if isinstance(bones, list) else []):
        if not isinstance(bone, dict):
            errors.append(f"bones[{index}] must be an object")
            continue
        parent = bone.get("parent")
        if parent is not None and (not isinstance(parent, str) or parent.casefold() not in bone_names):
            errors.append(f"bone {bone.get('name', index)} references missing parent {parent}")
    for name, bone in bone_by_name.items():
        seen = {name}
        parent = bone.get("parent")
        while isinstance(parent, str) and parent.casefold() in bone_by_name:
            key = parent.casefold()
            if key in seen:
                errors.append(f"bone hierarchy contains a cycle at {bone.get('name')}")
                break
            seen.add(key)
            parent = bone_by_name[key].get("parent")
    if not bone_names:
        errors.append("at least one bone is required, including for static models")

    bone_renames = contract["bone_renames"]
    rename_sources: set[str] = set()
    rename_targets: set[str] = set()
    if not isinstance(bone_renames, list):
        errors.append("bone_renames must be a list")
        bone_renames = []
    for index, rename in enumerate(bone_renames):
        label = f"bone_renames[{index}]"
        if not isinstance(rename, dict):
            errors.append(f"{label} must be an object")
            continue
        source = rename.get("source")
        target = rename.get("target")
        if not isinstance(source, str) or not source.strip():
            errors.append(f"{label}.source must be a non-empty bone name")
            continue
        if not isinstance(target, str) or not target.strip():
            errors.append(f"{label}.target must be a non-empty bone name")
            continue
        source_key, target_key = source.casefold(), target.casefold()
        if source_key == target_key:
            errors.append(f"{label} may not rename a bone to itself")
        if source_key in rename_sources:
            errors.append(f"duplicate bone rename source: {source}")
        if target_key in rename_targets:
            errors.append(f"duplicate bone rename target: {target}")
        if source_key in bone_names:
            errors.append(f"bone rename source conflicts with a final contract bone: {source}")
        if target_key not in bone_names:
            errors.append(f"bone rename target is absent from final contract bones: {target}")
        rename_sources.add(source_key)
        rename_targets.add(target_key)
    overlap = sorted(rename_sources & rename_targets)
    if overlap:
        errors.append(f"bone rename chains or cycles are not supported: {', '.join(overlap)}")

    compatibility = contract.get("compatibility")
    if compatibility is not None:
        if not isinstance(compatibility, dict):
            errors.append("compatibility must be an object")
        else:
            unknown = sorted(set(compatibility) - {"role", "baseline_mdl"})
            if unknown:
                errors.append(f"unsupported compatibility fields: {', '.join(unknown)}")
            if compatibility.get("role") not in {"player", "npc"}:
                errors.append("compatibility.role must be player or npc")
            _safe_relative(compatibility.get("baseline_mdl"), "compatibility.baseline_mdl", errors, suffix=".mdl")

    source_paths: list[tuple[str, str, bool]] = []
    body_names = _unique_names(contract["bodies"], "bodies", errors)
    for index, body in enumerate(contract["bodies"]):
        if not isinstance(body, dict):
            errors.append(f"bodies[{index}] must be an object")
            continue
        source = _safe_relative(body.get("source"), f"bodies[{index}].source", errors, suffix=".smd")
        if source:
            source_paths.append((f"bodies[{index}]", source, True))
        if not isinstance(body.get("object"), str) or not body["object"].strip():
            errors.append(f"bodies[{index}].object must name a Blender mesh object")

    group_names = _unique_names(contract["bodygroups"], "bodygroups", errors)
    if body_names & group_names:
        errors.append("body and bodygroup names must be unique")
    for group_index, group in enumerate(contract["bodygroups"]):
        if not isinstance(group, dict):
            errors.append(f"bodygroups[{group_index}] must be an object")
            continue
        choices = group.get("choices")
        if not isinstance(choices, list) or not choices:
            errors.append(f"bodygroup {group.get('name', group_index)} must have at least one choice")
            continue
        for choice_index, choice in enumerate(choices):
            label = f"bodygroups[{group_index}].choices[{choice_index}]"
            if not isinstance(choice, dict):
                errors.append(f"{label} must be an object")
                continue
            has_blank = choice.get("blank") is True
            has_studio = "studio" in choice
            if has_blank == has_studio:
                errors.append(f"{label} must declare exactly one of blank=true or studio")
                continue
            if has_studio:
                source = _safe_relative(choice.get("studio"), f"{label}.studio", errors, suffix=".smd")
                if source:
                    source_paths.append((label, source, True))
                if not isinstance(choice.get("object"), str) or not choice["object"].strip():
                    errors.append(f"{label}.object must name a Blender mesh object")

    large_texture_names: set[str] = set()
    large_textures = contract["large_textures"]
    if not isinstance(large_textures, list):
        errors.append("large_textures must be a list")
        large_textures = []
    for index, atlas in enumerate(large_textures):
        if not isinstance(atlas, dict):
            errors.append(f"large_textures[{index}] must be an object")
            continue
        try:
            normalized_atlas = validate_large_texture_spec(atlas)
        except LargeTextureError as exc:
            errors.append(f"large_textures[{index}]: {exc}")
            continue
        name = str(normalized_atlas["name"])
        key = name.casefold()
        if key in large_texture_names:
            errors.append(f"duplicate large texture name: {name}")
        large_texture_names.add(key)

    texture_names = _unique_names(contract["textures"], "textures", errors)
    namespace_collisions = sorted(large_texture_names & texture_names)
    if namespace_collisions:
        errors.append(
            "logical large-texture names must not collide with physical texture names: "
            + ", ".join(namespace_collisions)
        )
    if isinstance(contract["textures"], list) and len(contract["textures"]) > GOLDSRC_MAX_TEXTURES_PER_MODEL:
        errors.append(
            "GoldSrc texture budget exceeded across the complete MDL: "
            f"{len(contract['textures'])} > {GOLDSRC_MAX_TEXTURES_PER_MODEL}"
        )
    textures = {item.get("name", "").casefold(): item for item in contract["textures"] if isinstance(item, dict)}
    for index, texture in enumerate(contract["textures"]):
        if not isinstance(texture, dict):
            errors.append(f"textures[{index}] must be an object")
            continue
        name = texture.get("name")
        if isinstance(name, str) and Path(name).suffix.casefold() != ".bmp":
            errors.append(f"texture name must end with .bmp: {name}")
        source = _safe_relative(texture.get("source", name), f"textures[{index}].source", errors, suffix=".bmp")
        texture["source"] = source or texture.get("source", name)
        width, height = texture.get("width"), texture.get("height")
        if not all(isinstance(item, int) and not isinstance(item, bool) and 0 < item <= 512 and item % 16 == 0 for item in (width, height)):
            errors.append(f"texture {name or index} dimensions must be integer multiples of 16 within 1..512")
        modes = texture.setdefault("modes", [])
        if not isinstance(modes, list) or len(modes) != len(set(modes)) or any(mode not in TEXTURE_MODES for mode in modes):
            errors.append(f"texture {name or index} has unsupported modes")
        else:
            effective_modes = effective_texture_modes(texture)
            if contract["target_profile"] == "half-life-cs" and "fullbright" in effective_modes:
                errors.append(f"texture {name or index} uses Sven/Xash3D-only fullbright in half-life-cs profile")
            if contract["target_profile"] == "half-life-cs" and "chrome" in effective_modes and (width, height) != (64, 64):
                errors.append(f"texture {name or index} must be 64x64 for Half-Life/Counter-Strike chrome")

    families = contract["skin_families"]
    if families:
        if not all(isinstance(row, list) for row in families):
            errors.append("every skin family must be a list")
        else:
            widths = {len(row) for row in families}
            if len(widths) != 1 or 0 in widths:
                errors.append("skin family rows must be non-empty and have identical lengths")
            for family_index, row in enumerate(families):
                for slot, texture_name in enumerate(row):
                    key = texture_name.casefold() if isinstance(texture_name, str) else ""
                    if key not in texture_names:
                        errors.append(f"skin family {family_index} slot {slot} references missing texture {texture_name}")
            if len(widths) == 1 and 0 not in widths:
                width = next(iter(widths))
                for slot in range(width):
                    dimensions = {
                        (textures[row[slot].casefold()].get("width"), textures[row[slot].casefold()].get("height"))
                        for row in families if isinstance(row[slot], str) and row[slot].casefold() in textures
                    }
                    if len(dimensions) > 1:
                        errors.append(f"skin slot {slot} textures do not share dimensions")

    sequence_names = _unique_names(contract["sequences"], "sequences", errors)
    physics = contract.get("physics")
    physics_simulation = physics.get("simulation", {}) if isinstance(physics, dict) else {}
    physics_sequence = physics_simulation.get("sequence") if isinstance(physics_simulation, dict) else None
    physics_export_fps = physics_simulation.get("export_fps") if isinstance(physics_simulation, dict) else None
    if physics_sequence is not None:
        matching_sequences = [item for item in contract["sequences"] if isinstance(item, dict) and item.get("name", "").casefold() == str(physics_sequence).casefold()]
        if not matching_sequences:
            errors.append(f"physics.simulation.sequence references missing sequence: {physics_sequence}")
        elif physics_export_fps is not None and _is_number(physics_export_fps) and abs(float(matching_sequences[0].get("fps", 0)) - float(physics_export_fps)) > 1e-6:
            errors.append("physics.simulation.sequence FPS must equal physics.simulation.export_fps")
    for index, sequence in enumerate(contract["sequences"]):
        if not isinstance(sequence, dict):
            errors.append(f"sequences[{index}] must be an object")
            continue
        source = _safe_relative(sequence.get("source"), f"sequences[{index}].source", errors, suffix=".smd")
        if source:
            source_paths.append((f"sequences[{index}]", source, False))
        if not isinstance(sequence.get("action"), str) or not sequence["action"].strip():
            errors.append(f"sequences[{index}].action must name a Blender Action")
        fps = sequence.get("fps")
        if not _is_number(fps) or not 0 < float(fps) <= 120:
            errors.append(f"sequence {sequence.get('name', index)} fps must be within 0..120")
        frame_range = sequence.get("frame")
        if frame_range is not None and (not isinstance(frame_range, list) or len(frame_range) != 2 or not all(isinstance(item, int) for item in frame_range) or frame_range[0] > frame_range[1]):
            errors.append(f"sequence {sequence.get('name', index)} has an invalid frame range")
        motion = sequence.setdefault("motion", [])
        if not isinstance(motion, list) or any(axis not in MOTION_AXES for axis in motion):
            errors.append(f"sequence {sequence.get('name', index)} has an invalid motion axis")
        origin = sequence.get("origin")
        if origin is not None and not _is_vec3(origin):
            errors.append(f"sequence {sequence.get('name', index)} has an invalid origin")
        activity = sequence.get("activity")
        if activity is not None:
            if not isinstance(activity, dict):
                errors.append(f"sequence {sequence.get('name', index)} activity must be an object")
            else:
                activity_name = activity.get("name")
                if not isinstance(activity_name, str) or not _NAME.match(activity_name):
                    errors.append(
                        f"sequence {sequence.get('name', index)} activity name must use letters, digits, dot, underscore, or dash"
                    )
                weight = activity.get("weight", 1)
                if not isinstance(weight, int) or isinstance(weight, bool) or weight < 0:
                    errors.append(f"sequence {sequence.get('name', index)} activity weight must be a non-negative integer")
        for event_index, event in enumerate(sequence.setdefault("events", [])):
            if not isinstance(event, dict) or not isinstance(event.get("frame"), int) or not isinstance(event.get("id"), int):
                errors.append(f"sequence {sequence.get('name', index)} event {event_index} is invalid")
                continue
            options = event.get("options", "")
            if not isinstance(options, str):
                errors.append(f"sequence {sequence.get('name', index)} event {event_index} options must be a string")
            else:
                _qc_text(
                    options,
                    f"sequences[{index}].events[{event_index}].options",
                    errors,
                    allow_empty=True,
                )
            if frame_range and not frame_range[0] <= event["frame"] <= frame_range[1]:
                errors.append(f"sequence {sequence.get('name', index)} event {event_index} is outside its frame range")

    for label, items in (("hitboxes", contract["hitboxes"]), ("attachments", contract["attachments"]), ("controllers", contract["controllers"])):
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append(f"{label}[{index}] must be an object")
                continue
            bone = item.get("bone")
            if not isinstance(bone, str) or bone.casefold() not in bone_names:
                errors.append(f"{label}[{index}] references missing bone {bone}")
            if label == "hitboxes" and (not _is_vec3(item.get("min")) or not _is_vec3(item.get("max"))):
                errors.append(f"hitboxes[{index}] requires numeric min/max vectors")
            elif label == "hitboxes" and any(item["min"][axis] > item["max"][axis] for axis in range(3)):
                errors.append(f"hitboxes[{index}] min must not exceed max")
            if label == "attachments" and (not isinstance(item.get("index"), int) or not _is_vec3(item.get("origin"))):
                errors.append(f"attachments[{index}] requires integer index and numeric origin")
            if label == "controllers" and (item.get("type") not in CONTROLLER_AXES or not isinstance(item.get("index"), int) or not _is_number(item.get("start")) or not _is_number(item.get("end"))):
                errors.append(f"controllers[{index}] has invalid index, type, or range")
            elif label == "controllers" and float(item["start"]) >= float(item["end"]):
                errors.append(f"controllers[{index}] start must be less than end")
    attachment_indices = [item.get("index") for item in contract["attachments"] if isinstance(item, dict)]
    if len(attachment_indices) != len(set(attachment_indices)) or any(not isinstance(index, int) or not 0 <= index <= 3 for index in attachment_indices):
        errors.append("attachment indices must be unique within 0..3")
    controller_indices = [item.get("index") for item in contract["controllers"] if isinstance(item, dict)]
    if len(controller_indices) != len(set(controller_indices)) or any(not isinstance(index, int) or not 0 <= index <= 4 for index in controller_indices):
        errors.append("controller indices must be unique within 0..4")

    for index, rename in enumerate(bone_renames):
        if isinstance(rename, dict):
            _qc_text(rename.get("source"), f"bone_renames[{index}].source", errors)
            _qc_text(rename.get("target"), f"bone_renames[{index}].target", errors)

    bounds = contract.get("bounds")
    if not isinstance(bounds, dict):
        errors.append("bounds must define bbox and cbox")
    else:
        for kind in ("bbox", "cbox"):
            box = bounds.get(kind)
            if not isinstance(box, dict) or not _is_vec3(box.get("min")) or not _is_vec3(box.get("max")):
                errors.append(f"bounds.{kind} requires numeric min/max vectors")
            elif any(box["min"][axis] > box["max"][axis] for axis in range(3)):
                errors.append(f"bounds.{kind} min must not exceed max")

    output_paths: list[str] = []
    for name, output in contract["outputs"].items():
        expected = ".mdl" if name.endswith("mdl") else ".qc" if name.endswith("qc") else ".json" if name == "report" else None
        normalized_output = _safe_relative(output, f"outputs.{name}", errors, suffix=expected)
        if normalized_output:
            output_paths.append(normalized_output.casefold())
    if len(output_paths) != len(set(output_paths)):
        errors.append("outputs must use distinct paths so validation builds cannot overwrite production artifacts")
    phases = contract["acceptance"].get("required_phases")
    if not isinstance(phases, list) or len(phases) != len(set(phases)) or any(phase not in DEFAULT_PHASES for phase in phases):
        errors.append("acceptance.required_phases contains duplicates or unknown phases")

    root = Path(artifact_dir).expanduser().resolve() if artifact_dir is not None else None
    if require_files and root is None:
        errors.append("artifact_dir is required when require_files=true")
    if require_files and root:
        compatibility = contract.get("compatibility")
        if isinstance(compatibility, dict) and isinstance(compatibility.get("baseline_mdl"), str):
            baseline_path = (root / compatibility["baseline_mdl"]).resolve()
            if root not in baseline_path.parents:
                errors.append("compatibility.baseline_mdl escapes artifact directory")
            elif not baseline_path.is_file():
                errors.append(f"compatibility.baseline_mdl is missing: {compatibility['baseline_mdl']}")
            else:
                try:
                    from .mdl_v10 import inspect_mdl
                    inspect_mdl(baseline_path)
                except (OSError, ValueError) as exc:
                    errors.append(f"compatibility.baseline_mdl is not a valid MDL v10: {exc}")
        revision = contract.get("intent", {}).get("revision") if isinstance(contract.get("intent"), dict) else None
        if isinstance(revision, dict) and isinstance(revision.get("baseline_report"), str):
            baseline_path = (root / revision["baseline_report"]).resolve()
            if root not in baseline_path.parents:
                errors.append("intent.revision.baseline_report escapes artifact directory")
            elif not baseline_path.is_file():
                errors.append(f"intent.revision.baseline_report is missing: {revision['baseline_report']}")
            else:
                try:
                    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
                    if baseline.get("status") not in {"pass", "pass_with_known_blockers"}:
                        errors.append("intent.revision.baseline_report is not a passing pipeline report")
                except (OSError, json.JSONDecodeError, AttributeError) as exc:
                    errors.append(f"intent.revision.baseline_report is invalid: {exc}")
        documents = []
        for label, relative, require_triangles in source_paths:
            path = (root / relative).resolve()
            if root not in path.parents:
                errors.append(f"{label} escapes artifact directory")
                continue
            if not path.is_file():
                errors.append(f"{label} file is missing: {relative}")
                continue
            try:
                document = read_smd(path)
                for message in validate_smd(document, require_triangles=require_triangles):
                    errors.append(f"{label}: {message}")
                documents.append((label, document))
            except (OSError, SmdError, ValueError) as exc:
                errors.append(f"{label}: {exc}")
        referenced_materials = {material.casefold() for _label, document in documents for material in document.materials}
        for label, document in documents:
            if label.startswith("sequences["):
                continue
            budget = geometry_budget(document, target_profile=contract["target_profile"])
            if budget["hard_failure"]:
                if budget["compiled_vertices"] > budget["vertex_limit"]:
                    errors.append(
                        f"{label} GoldSrc compiled vertex budget exceeded: "
                        f"{budget['compiled_vertices']} > {budget['vertex_limit']}"
                    )
                if budget["compiled_normals"] > budget["normal_limit"]:
                    errors.append(
                        f"{label} GoldSrc compiled normal budget exceeded: "
                        f"{budget['compiled_normals']} > {budget['normal_limit']}"
                    )
                if budget["triangles"] > budget["triangle_limit"]:
                    errors.append(
                        f"{label} GoldSrc triangle budget exceeded: "
                        f"{budget['triangles']} > {budget['triangle_limit']}"
                    )
        missing_materials = referenced_materials - (texture_names | large_texture_names)
        for material in sorted(missing_materials):
            errors.append(f"SMD material is absent from textures: {material}")
        rename_map = {
            item["source"].casefold(): item["target"].casefold()
            for item in bone_renames
            if isinstance(item, dict) and isinstance(item.get("source"), str) and isinstance(item.get("target"), str)
        }

        def canonical_bone_name(name: str) -> str:
            key = name.casefold()
            return rename_map.get(key, key)

        expected_bones = {(item["name"].casefold(), item.get("parent").casefold() if isinstance(item.get("parent"), str) else None) for item in bones if isinstance(item, dict) and isinstance(item.get("name"), str)}
        for label, document in documents:
            id_to_name = {bone.index: canonical_bone_name(bone.name) for bone in document.bones}
            # Source Tools emits this helper node in SMD skeleton blocks, while
            # StudioMDL excludes it from the compiled GoldSrc bone table.
            actual = {
                (canonical_bone_name(bone.name), id_to_name.get(bone.parent))
                for bone in document.bones
                if bone.name.casefold() != "blender_implicit"
            }
            if actual != expected_bones:
                errors.append(f"{label} skeleton does not match contract bones")
        sequence_documents = {
            label: document for label, document in documents if label.startswith("sequences[")
        }
        for index, sequence in enumerate(contract["sequences"]):
            document = sequence_documents.get(f"sequences[{index}]")
            if not document or not document.frames:
                continue
            allowed_start, allowed_end = sequence.get("frame", [min(document.frames), max(document.frames)])
            if allowed_start < min(document.frames) or allowed_end > max(document.frames):
                errors.append(f"sequence {sequence['name']} frame range exceeds its SMD frames")
            for event_index, event in enumerate(sequence.get("events", [])):
                if not allowed_start <= event["frame"] <= allowed_end:
                    errors.append(f"sequence {sequence['name']} event {event_index} is outside exported frames")
        for index, texture in enumerate(contract["textures"]):
            if not isinstance(texture, dict) or not isinstance(texture.get("source"), str):
                continue
            try:
                validate_indexed_bmp(root / texture["source"], width=texture.get("width"), height=texture.get("height"), modes=texture.get("modes", []), require_masked_pixels=texture.get("require_masked_pixels", True))
            except (OSError, TextureError) as exc:
                errors.append(f"textures[{index}]: {exc}")
    if errors:
        raise ContractError(errors)
    return contract


def load_contract(path: Path | str, *, artifact_dir: Path | str | None = None, require_files: bool = False) -> dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError([f"cannot read contract {resolved}: {exc}"]) from exc
    return validate_contract(value, artifact_dir=artifact_dir or resolved.parent, require_files=require_files)


def _quoted_source(path: str) -> str:
    normalized = path.replace("\\", "/")
    return normalized[:-4] if normalized.casefold().endswith(".smd") else normalized


def _box_line(name: str, box: dict[str, list[float]]) -> str:
    values = [*box["min"], *box["max"]]
    return f"${name} " + " ".join(f"{float(value):g}" for value in values)


def render_qc(contract: dict[str, Any]) -> str:
    contract = validate_contract(contract)
    lines = [
        f'$modelname "{contract["outputs"]["sven_mdl"]}"',
        '$cd "."',
        '$cdtexture "."',
        f'$scale {float(contract["scale"]):g}',
        _box_line("bbox", contract["bounds"]["bbox"]),
        _box_line("cbox", contract["bounds"]["cbox"]),
    ]
    for rename in contract["bone_renames"]:
        lines.append(f'$renamebone "{rename["source"]}" "{rename["target"]}"')
    for body in contract["bodies"]:
        sources = body.get("_compiled_sources") or [body["source"]]
        for index, source in enumerate(sources):
            name = body["name"] if len(sources) == 1 else f"{body['name']}_part{index + 1:03d}"
            lines.append(f'$body "{name}" "{_quoted_source(source)}"')
    for group in contract["bodygroups"]:
        lines.extend([f'$bodygroup "{group["name"]}"', "{"])
        for choice in group["choices"]:
            lines.append("    blank" if choice.get("blank") is True else f'    studio "{_quoted_source(choice["studio"])}"')
        lines.append("}")
    if contract["skin_families"]:
        lines.extend(['$texturegroup "skinfamilies"', "{"])
        for family in contract["skin_families"]:
            lines.append("    { " + " ".join(f'"{name}"' for name in family) + " }")
        lines.append("}")
    texture_mode_lines = [
        (mode, f'$texrendermode "{texture["name"]}" {mode}')
        for texture in contract["textures"]
        for mode in texture.get("modes", [])
    ]
    lines.extend(line for mode, line in texture_mode_lines if mode != "masked")
    lines.extend(line for mode, line in texture_mode_lines if mode == "masked")
    for controller in contract["controllers"]:
        lines.append(f'$controller {controller["index"]} "{controller["bone"]}" {controller["type"]} {float(controller["start"]):g} {float(controller["end"]):g}')
    for hitbox in contract["hitboxes"]:
        values = [*hitbox["min"], *hitbox["max"]]
        lines.append(f'$hbox {int(hitbox.get("group", 0))} "{hitbox["bone"]}" ' + " ".join(f"{float(value):g}" for value in values))
    for attachment in contract["attachments"]:
        lines.append(f'$attachment {attachment["index"]} "{attachment["bone"]}" ' + " ".join(f"{float(value):g}" for value in attachment["origin"]))
    for sequence in contract["sequences"]:
        parts = [f'$sequence "{sequence["name"]}" "{_quoted_source(sequence["source"])}"']
        if sequence.get("frame"):
            parts.extend(["frame", str(sequence["frame"][0]), str(sequence["frame"][1])])
        parts.extend(["fps", f'{float(sequence["fps"]):g}'])
        if sequence.get("loop"):
            parts.append("loop")
        parts.extend(sequence.get("motion", []))
        if sequence.get("origin"):
            parts.extend(["origin", *(f"{float(value):g}" for value in sequence["origin"])])
        activity = sequence.get("activity")
        if isinstance(activity, dict):
            parts.extend([str(activity["name"]), str(int(activity.get("weight", 1)))])
        if sequence.get("events"):
            parts.append("{")
            for event in sequence["events"]:
                parts.extend(["event", str(event["id"]), str(event["frame"])])
                if event.get("options"):
                    parts.append(f'"{event["options"]}"')
            parts.append("}")
        lines.append(" ".join(parts))
    return "\n".join(lines) + "\n"


def write_qc(contract: dict[str, Any], artifact_dir: Path | str) -> Path:
    normalized = validate_contract(contract)
    root = Path(artifact_dir).expanduser().resolve()
    path = (root / normalized["outputs"]["qc"]).resolve()
    if root not in path.parents:
        raise ContractError([f"QC output escapes artifact directory: {path}"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_qc(normalized), encoding="utf-8")
    return path


def contract_summary(contract: dict[str, Any]) -> dict[str, Any]:
    normalized = validate_contract(contract)
    return {
        "contract_version": normalized["version"],
        "target_profile": normalized["target_profile"],
        "model_name": normalized["model_name"],
        "features": {
            "bones": len(normalized["bones"]), "bodies": len(normalized["bodies"]),
            "bone_renames": len(normalized["bone_renames"]),
            "bodygroups": len(normalized["bodygroups"]), "skin_families": len(normalized["skin_families"]),
            "textures": len(normalized["textures"]), "sequences": len(normalized["sequences"]),
            "hitboxes": len(normalized["hitboxes"]), "attachments": len(normalized["attachments"]),
            "controllers": len(normalized["controllers"]),
            "physics_mode": normalized.get("physics", {}).get("mode") if isinstance(normalized.get("physics"), dict) else None,
            "physics_stages": len(normalized.get("physics", {}).get("stages", [])) if isinstance(normalized.get("physics"), dict) else 0,
            "physics_interactions": len(normalized.get("physics", {}).get("interactions", [])) if isinstance(normalized.get("physics"), dict) else 0,
            "requirements": len(normalized.get("intent", {}).get("requirements", [])) if isinstance(normalized.get("intent"), dict) else 0,
            "revision": bool(normalized.get("intent", {}).get("revision")) if isinstance(normalized.get("intent"), dict) else False,
            "compatibility_role": normalized.get("compatibility", {}).get("role") if isinstance(normalized.get("compatibility"), dict) else None,
        },
    }
