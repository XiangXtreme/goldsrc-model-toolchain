"""Safe GoldSrc MDL v10 decompilation using the independent readback parser."""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from pathlib import Path

import numpy as np

from .errors import ToolchainError
from .mdl_v10 import inspect_mdl
from ..vendor.sourceio_goldsrc import read_mdl


TEXTURE_MODES = (
    (0x0001, "flatshade"), (0x0002, "chrome"), (0x0004, "fullbright"),
    (0x0008, "nomips"), (0x0010, "alpha"), (0x0020, "additive"),
    (0x0040, "masked"),
)
MOTION_FLAGS = (
    (0x0001, "X"), (0x0002, "Y"), (0x0004, "Z"),
    (0x0008, "XR"), (0x0010, "YR"), (0x0020, "ZR"),
    (0x0040, "LX"), (0x0080, "LY"), (0x0100, "LZ"),
    (0x0200, "AX"), (0x0400, "AY"), (0x0800, "AZ"),
    (0x1000, "AXR"), (0x2000, "AYR"), (0x4000, "AZR"),
)
ACTIVITIES = (
    "ACT_RESET", "ACT_IDLE", "ACT_GUARD", "ACT_WALK", "ACT_RUN", "ACT_FLY",
    "ACT_SWIM", "ACT_HOP", "ACT_LEAP", "ACT_FALL", "ACT_LAND", "ACT_STRAFE_LEFT",
    "ACT_STRAFE_RIGHT", "ACT_ROLL_LEFT", "ACT_ROLL_RIGHT", "ACT_TURN_LEFT",
    "ACT_TURN_RIGHT", "ACT_CROUCH", "ACT_CROUCHIDLE", "ACT_STAND", "ACT_USE",
    "ACT_SIGNAL1", "ACT_SIGNAL2", "ACT_SIGNAL3", "ACT_TWITCH", "ACT_COWER",
    "ACT_SMALL_FLINCH", "ACT_BIG_FLINCH", "ACT_RANGE_ATTACK1", "ACT_RANGE_ATTACK2",
    "ACT_MELEE_ATTACK1", "ACT_MELEE_ATTACK2", "ACT_RELOAD", "ACT_ARM", "ACT_DISARM",
    "ACT_EAT", "ACT_DIESIMPLE", "ACT_DIEBACKWARD", "ACT_DIEFORWARD", "ACT_DIEVIOLENT",
    "ACT_BARNACLE_HIT", "ACT_BARNACLE_PULL", "ACT_BARNACLE_CHOMP", "ACT_BARNACLE_CHEW",
    "ACT_SLEEP", "ACT_INSPECT_FLOOR", "ACT_INSPECT_WALL", "ACT_IDLE_ANGRY",
    "ACT_WALK_HURT", "ACT_RUN_HURT", "ACT_HOVER", "ACT_GLIDE", "ACT_FLY_LEFT",
    "ACT_FLY_RIGHT", "ACT_DETECT_SCENT", "ACT_SNIFF", "ACT_BITE", "ACT_THREAT_DISPLAY",
    "ACT_FEAR_DISPLAY", "ACT_EXCITED", "ACT_SPECIAL_ATTACK1", "ACT_SPECIAL_ATTACK2",
    "ACT_COMBAT_IDLE", "ACT_WALK_SCARED", "ACT_RUN_SCARED", "ACT_VICTORY_DANCE",
    "ACT_DIE_HEADSHOT", "ACT_DIE_CHESTSHOT", "ACT_DIE_GUTSHOT", "ACT_DIE_BACKSHOT",
    "ACT_FLINCH_HEAD", "ACT_FLINCH_CHEST", "ACT_FLINCH_STOMACH", "ACT_FLINCH_LEFTARM",
    "ACT_FLINCH_RIGHTARM", "ACT_FLINCH_LEFTLEG", "ACT_FLINCH_RIGHTLEG",
)


def _safe_name(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned or fallback


def _new_text(path: Path, text: str) -> None:
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except FileExistsError as exc:
        raise ToolchainError(
            "DECOMPILE", "decompile.output_exists",
            "Decompile output already exists; choose a new artifact directory",
            {"path": str(path)},
        ) from exc


def _new_bytes(path: Path, data: bytes) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(data)
    except FileExistsError as exc:
        raise ToolchainError(
            "DECOMPILE", "decompile.output_exists",
            "Decompile output already exists; choose a new artifact directory",
            {"path": str(path)},
        ) from exc


def _local_matrix(position, rotation) -> np.ndarray:
    x, y, z = rotation
    cx, sx = math.cos(x), math.sin(x)
    cy, sy = math.cos(y), math.sin(y)
    cz, sz = math.cos(z), math.sin(z)
    rx = np.array(((1, 0, 0), (0, cx, -sx), (0, sx, cx)), dtype=np.float64)
    ry = np.array(((cy, 0, sy), (0, 1, 0), (-sy, 0, cy)), dtype=np.float64)
    rz = np.array(((cz, -sz, 0), (sz, cz, 0), (0, 0, 1)), dtype=np.float64)
    matrix = np.identity(4, dtype=np.float64)
    matrix[:3, :3] = rz @ ry @ rx
    matrix[:3, 3] = position
    return matrix


def _bone_globals(bones) -> list[np.ndarray]:
    result = []
    for bone in bones:
        local = _local_matrix(bone.position, bone.rotation)
        result.append(result[bone.parent] @ local if bone.parent >= 0 else local)
    return result


def _nodes(bones) -> str:
    lines = ["version 1", "nodes"]
    lines.extend(f'{index} "{bone.name.replace(chr(34), "_")}" {bone.parent}' for index, bone in enumerate(bones))
    lines.append("end")
    return "\n".join(lines) + "\n"


def _reference_smd(mdl, model, skin_family) -> str:
    lines = [_nodes(mdl.bones).rstrip(), "skeleton", "time 0"]
    for index, bone in enumerate(mdl.bones):
        values = (*bone.position, *bone.rotation)
        lines.append(f"{index} " + " ".join(f"{value:.6f}" for value in values))
    lines.extend(("end", "triangles"))
    globals_by_bone = _bone_globals(mdl.bones)
    for mesh in model.meshes:
        texture_index = skin_family[mesh.skin_ref] if mesh.skin_ref < len(skin_family) else mesh.skin_ref
        if not 0 <= texture_index < len(mdl.textures):
            raise ToolchainError(
                "DECOMPILE", "decompile.skin_ref", "Mesh references a missing texture",
                {"model": model.name, "skin_ref": mesh.skin_ref},
            )
        texture = mdl.textures[texture_index]
        width = max(1, texture.width - 1)
        height = max(1, texture.height - 1)
        for command, fan in mesh.commands:
            triangles = []
            if fan:
                triangles = [(command[0], command[index + 1], command[index]) for index in range(1, len(command) - 1)]
            else:
                triangles = [
                    (command[index], command[index + 2 - (index & 1)], command[index + 1 + (index & 1)])
                    for index in range(len(command) - 2)
                ]
            for triangle in triangles:
                lines.append(texture.name)
                for item in triangle:
                    bone_index = int(model.bone_vertices[item.vertex])
                    normal_bone = int(model.bone_normals[item.normal])
                    position = globals_by_bone[bone_index] @ np.array((*model.vertices[item.vertex], 1.0))
                    normal = globals_by_bone[normal_bone][:3, :3] @ model.normals[item.normal]
                    length = float(np.linalg.norm(normal))
                    if length:
                        normal = normal / length
                    u = item.uv[0] / width
                    v = 1.0 - item.uv[1] / height
                    values = (*position[:3], *normal, u, v)
                    lines.append(f"{bone_index} " + " ".join(f"{float(value):.6f}" for value in values))
    lines.append("end")
    return "\n".join(lines) + "\n"


def _animation_smd(mdl, sequence, frames) -> str:
    lines = [_nodes(mdl.bones).rstrip(), "skeleton"]
    for frame_index, poses in enumerate(frames):
        lines.append(f"time {frame_index}")
        fraction = frame_index / (sequence.frame_count - 1) if sequence.frame_count > 1 else 0.0
        for bone_index, (position_value, rotation_value) in enumerate(poses):
            position = list(position_value)
            rotation = list(rotation_value)
            if mdl.bones[bone_index].parent == -1:
                if sequence.motion_type & 0x0040:
                    position[0] += fraction * sequence.linear_movement[0]
                if sequence.motion_type & 0x0080:
                    position[1] += fraction * sequence.linear_movement[1]
                if sequence.motion_type & 0x0100:
                    position[2] += fraction * sequence.linear_movement[2]
                position[0], position[1] = position[1], -position[0]
                rotation[2] -= math.pi / 2.0
            values = (*position, *rotation)
            lines.append(f"{bone_index} " + " ".join(f"{float(value):.6f}" for value in values))
    lines.append("end")
    return "\n".join(lines) + "\n"


def _indexed_bmp(texture) -> bytes:
    row_stride = (texture.width + 3) & ~3
    pixels = bytearray()
    padding = bytes(row_stride - texture.width)
    for row in np.flip(texture.indices, axis=0):
        pixels.extend(row.tobytes())
        pixels.extend(padding)
    palette = bytearray()
    for red, green, blue in texture.palette:
        palette.extend((int(blue), int(green), int(red), 0))
    pixel_offset = 14 + 40 + len(palette)
    file_size = pixel_offset + len(pixels)
    header = struct.pack("<2sIHHI", b"BM", file_size, 0, 0, pixel_offset)
    info = struct.pack(
        "<IiiHHIIiiII", 40, texture.width, texture.height, 1, 8, 0,
        len(pixels), 0, 0, 256, 256,
    )
    return header + info + bytes(palette) + bytes(pixels)


def _motion_tokens(value: int) -> list[str]:
    return [name for flag, name in MOTION_FLAGS if value & flag]


def _sequence_qc(sequence, smd_names: list[str]) -> list[str]:
    lines = [f'$sequence "{sequence.name.replace(chr(34), "_")}" {{']
    lines.extend(f'    "{Path(name).stem}"' for name in smd_names)
    if sequence.activity > 0:
        activity = ACTIVITIES[sequence.activity] if sequence.activity < len(ACTIVITIES) else f"ACT_{sequence.activity}"
        lines.append(f"    {activity} {sequence.activity_weight}")
    for index, blend_type in enumerate(sequence.blend_type):
        tokens = _motion_tokens(blend_type)
        if tokens:
            lines.append(f"    blend {tokens[0]} {sequence.blend_start[index]:.6g} {sequence.blend_end[index]:.6g}")
    for event in sequence.events:
        options = event.options.replace('"', "'")
        suffix = f' "{options}"' if options else ""
        lines.append(f"    {{ event {event.event} {event.frame}{suffix} }}")
    lines.append(f"    fps {sequence.fps:.6g}")
    if sequence.flags & 0x0001:
        lines.append("    loop")
    motion = _motion_tokens(sequence.motion_type)
    if motion:
        lines.append("    " + " ".join(motion))
    if sequence.entry_node and sequence.entry_node == sequence.exit_node:
        lines.append(f"    node {sequence.entry_node}")
    elif sequence.entry_node and sequence.exit_node:
        keyword = "rtransition" if sequence.node_flags else "transition"
        lines.append(f"    {keyword} {sequence.entry_node} {sequence.exit_node}")
    lines.append("}")
    return lines


def decompile_mdl(mdl_path: str | Path, artifacts_dir: str | Path) -> dict:
    source = Path(mdl_path).expanduser().resolve()
    root = Path(artifacts_dir).expanduser().resolve()
    if not source.is_file():
        raise ToolchainError("DECOMPILE", "decompile.missing_mdl", "MDL file is missing", {"path": str(source)})
    try:
        mdl = read_mdl(source)
        independent = inspect_mdl(source)
    except (OSError, ValueError) as exc:
        raise ToolchainError("DECOMPILE", "decompile.parse", str(exc), {"path": str(source)}) from exc
    external = [sequence.name for sequence in mdl.sequences if sequence.sequence_group]
    if external:
        raise ToolchainError(
            "DECOMPILE", "decompile.external_sequence_groups",
            "Safe decompilation requires embedded animation data; external sequence groups are unsupported",
            {"sequences": external},
        )
    if root.exists() and any(root.iterdir()):
        raise ToolchainError(
            "DECOMPILE", "decompile.output_not_empty",
            "Safe decompilation requires a new or empty artifact directory",
            {"artifacts_dir": str(root)},
        )
    root.mkdir(parents=True, exist_ok=True)
    files = []
    first_family = mdl.skin_families[0] if mdl.skin_families else list(range(len(mdl.textures)))
    body_sources = []
    for body_index, bodypart in enumerate(mdl.bodyparts):
        choices = []
        for model_index, model in enumerate(bodypart.models):
            if not len(model.vertices):
                choices.append(None)
                continue
            name = f"reference_{body_index:02d}_{model_index:02d}_{_safe_name(model.name, 'model')}.smd"
            _new_text(root / name, _reference_smd(mdl, model, first_family))
            files.append(name)
            choices.append(name)
        body_sources.append((bodypart, choices))
    sequence_sources = []
    for sequence_index, (sequence, blends) in enumerate(zip(mdl.sequences, mdl.animations)):
        names = []
        for blend_index, frames in enumerate(blends):
            name = f"animation_{sequence_index:03d}_{blend_index:02d}_{_safe_name(sequence.name, 'sequence')}.smd"
            _new_text(root / name, _animation_smd(mdl, sequence, frames))
            files.append(name)
            names.append(name)
        sequence_sources.append((sequence, names))
    for texture_index, texture in enumerate(mdl.textures):
        name = _safe_name(texture.name, f"texture_{texture_index}.bmp")
        if Path(name).suffix.casefold() != ".bmp":
            name += ".bmp"
        _new_bytes(root / name, _indexed_bmp(texture))
        files.append(name)
    qc_name = _safe_name(source.stem, "model") + ".qc"
    output_model = _safe_name(Path(mdl.header.name).name, source.name)
    if Path(output_model).suffix.casefold() != ".mdl":
        output_model += ".mdl"
    qc = [f'$modelname "{output_model}"', '$cd "."', '$cdtexture "."', "$scale 1.0", ""]
    for bodypart, choices in body_sources:
        body_name = _safe_name(bodypart.name, "body")
        if len(choices) == 1 and choices[0]:
            qc.append(f'$body "{body_name}" "{Path(choices[0]).stem}"')
        else:
            qc.extend((f'$bodygroup "{body_name}"', "{"))
            qc.extend("    blank" if choice is None else f'    studio "{Path(choice).stem}"' for choice in choices)
            qc.append("}")
    if mdl.skin_families:
        qc.extend(("", '$texturegroup "skinfamilies"', "{"))
        for family in mdl.skin_families:
            names = " ".join(f'"{mdl.textures[index].name}"' for index in family)
            qc.append(f"    {{ {names} }}")
        qc.append("}")
    mode_lines = []
    for texture in mdl.textures:
        for flag, mode in TEXTURE_MODES:
            if texture.flags & flag:
                mode_lines.append((mode, f'$texrendermode "{texture.name}" {mode}'))
    qc.extend(("", *(line for mode, line in mode_lines if mode != "masked"), *(line for mode, line in mode_lines if mode == "masked")))
    for sequence, names in sequence_sources:
        qc.append("")
        qc.extend(_sequence_qc(sequence, names))
    _new_text(root / qc_name, "\n".join(qc) + "\n")
    files.append(qc_name)
    manifest_name = "decompile_manifest.json"
    manifest = {
        "status": "pass",
        "source_mdl": str(source),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "artifacts_dir": str(root),
        "files": sorted([*files, manifest_name]),
        "counts": {
            "bones": len(mdl.bones), "bodyparts": len(mdl.bodyparts),
            "reference_smd": sum(choice is not None for _body, choices in body_sources for choice in choices),
            "sequences": len(mdl.sequences), "animation_smd": sum(len(names) for _sequence, names in sequence_sources),
            "textures": len(mdl.textures),
        },
        "cross_check": {
            "parser": "project mdl_v10 inspector",
            "bones": len(independent.get("bones", [])),
            "sequences": len(independent.get("sequences", [])),
            "textures": len(independent.get("textures", [])),
            "bodyparts": len(independent.get("bodyparts", [])),
        },
        "behavior_reference": "Crowbar SourceModel10; MDLDec/HL SDK used for historical structure checks",
        "uv_conversion": "Valve width-minus-one normalized SMD coordinates",
        "limitations": [],
        "manifest": str(root / manifest_name),
    }
    _new_text(root / manifest_name, json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return manifest
