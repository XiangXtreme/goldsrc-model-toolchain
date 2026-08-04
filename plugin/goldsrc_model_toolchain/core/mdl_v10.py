"""Structured GoldSrc MDL v10 inspection and texture-flag patching."""

from __future__ import annotations

import struct
from math import atan2, cos, pi, sin, sqrt
from pathlib import Path
from typing import Iterable

from .transforms import euler_xyz_rotation_error


TEXTURE_FLAGS = {
    "flatshade": 0x0001,
    "chrome": 0x0002,
    "fullbright": 0x0004,
    "nomips": 0x0008,
    "alpha": 0x0010,
    "additive": 0x0020,
    "masked": 0x0040,
}
KNOWN_TEXTURE_MASK = sum(TEXTURE_FLAGS.values())
CONTROLLER_TYPES = {"X": 0x0001, "Y": 0x0002, "Z": 0x0004, "XR": 0x0008, "YR": 0x0010, "ZR": 0x0020, "M": 0x0004}
MOTION_TYPES = {
    "X": 0x0001, "Y": 0x0002, "Z": 0x0004,
    "XR": 0x0008, "YR": 0x0010, "ZR": 0x0020,
    "LX": 0x0040, "LY": 0x0080, "LZ": 0x0100,
    "AX": 0x0200, "AY": 0x0400, "AZ": 0x0800,
    "AXR": 0x1000, "AYR": 0x2000, "AZR": 0x4000,
}
ACTIVITY_NAMES = (
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
ACTIVITY_IDS = {name.casefold(): index for index, name in enumerate(ACTIVITY_NAMES)}


class MdlError(ValueError):
    """Raised when an MDL is malformed or not GoldSrc version 10."""


def _cstring(data: bytes, offset: int, length: int) -> str:
    return data[offset : offset + length].split(b"\0", 1)[0].decode("latin-1", errors="replace")


def _range(data: bytes, offset: int, count: int, stride: int, label: str) -> range:
    if count < 0 or offset < 0 or offset + count * stride > len(data):
        raise MdlError(f"invalid {label} table: count={count}, offset={offset}")
    return range(count)


def inspect_mdl(path: Path | str) -> dict:
    resolved = Path(path).expanduser().resolve()
    data = resolved.read_bytes()
    if len(data) < 244 or data[:4] != b"IDST" or struct.unpack_from("<i", data, 4)[0] != 10:
        raise MdlError(f"not a GoldSrc MDL v10: {resolved}")
    header_values = struct.unpack_from("<26i", data, 140)
    header = {
        "name": _cstring(data, 8, 64),
        "length": struct.unpack_from("<i", data, 72)[0],
        "eye_position": list(struct.unpack_from("<3f", data, 76)),
        "min": list(struct.unpack_from("<3f", data, 88)),
        "max": list(struct.unpack_from("<3f", data, 100)),
        "bbmin": list(struct.unpack_from("<3f", data, 112)),
        "bbmax": list(struct.unpack_from("<3f", data, 124)),
        "flags": struct.unpack_from("<i", data, 136)[0],
    }
    keys = (
        "bone_count", "bone_offset", "controller_count", "controller_offset",
        "hitbox_count", "hitbox_offset", "sequence_count", "sequence_offset",
        "sequence_group_count", "sequence_group_offset", "texture_count", "texture_offset",
        "texture_data_offset", "skin_ref_count", "skin_family_count", "skin_offset",
        "bodypart_count", "bodypart_offset", "attachment_count", "attachment_offset",
        "sound_count", "sound_offset", "sound_group_count", "sound_group_offset",
        "transition_count", "transition_offset",
    )
    header.update(dict(zip(keys, header_values)))
    if header["length"] != len(data):
        raise MdlError(f"MDL header length {header['length']} differs from file size {len(data)}")

    bones = []
    for index in _range(data, header["bone_offset"], header["bone_count"], 112, "bone"):
        offset = header["bone_offset"] + index * 112
        parent, flags = struct.unpack_from("<2i", data, offset + 32)
        controllers = list(struct.unpack_from("<6i", data, offset + 40))
        values = list(struct.unpack_from("<6f", data, offset + 64))
        scales = list(struct.unpack_from("<6f", data, offset + 88))
        bones.append({"index": index, "name": _cstring(data, offset, 32), "parent": parent, "flags": flags, "controllers": controllers, "values": values, "scales": scales})

    controllers = []
    for index in _range(data, header["controller_offset"], header["controller_count"], 24, "controller"):
        offset = header["controller_offset"] + index * 24
        bone, controller_type, start, end, rest, controller_index = struct.unpack_from("<iiffii", data, offset)
        controllers.append({"bone": bone, "type": controller_type, "start": start, "end": end, "rest": rest, "index": controller_index})

    hitboxes = []
    for index in _range(data, header["hitbox_offset"], header["hitbox_count"], 32, "hitbox"):
        offset = header["hitbox_offset"] + index * 32
        bone, group = struct.unpack_from("<2i", data, offset)
        hitboxes.append({"bone": bone, "group": group, "min": list(struct.unpack_from("<3f", data, offset + 8)), "max": list(struct.unpack_from("<3f", data, offset + 20))})

    sequences = []
    for index in _range(data, header["sequence_offset"], header["sequence_count"], 176, "sequence"):
        offset = header["sequence_offset"] + index * 176
        fps = struct.unpack_from("<f", data, offset + 32)[0]
        flags, activity, weight, event_count, event_offset, frame_count = struct.unpack_from("<6i", data, offset + 36)
        events = []
        for event_index in _range(data, event_offset, event_count, 76, f"sequence {index} event"):
            current = event_offset + event_index * 76
            frame, event_id, event_type = struct.unpack_from("<3i", data, current)
            events.append({"frame": frame, "id": event_id, "type": event_type, "options": _cstring(data, current + 12, 64)})
        sequences.append({
            "name": _cstring(data, offset, 32), "fps": fps, "flags": flags,
            "loop": bool(flags & 1), "activity": activity, "activity_weight": weight,
            "frame_count": frame_count, "motion_type": struct.unpack_from("<i", data, offset + 68)[0],
            "motion_bone": struct.unpack_from("<i", data, offset + 72)[0],
            "linear_movement": list(struct.unpack_from("<3f", data, offset + 76)), "events": events,
            "blend_count": struct.unpack_from("<i", data, offset + 120)[0],
            "animation_offset": struct.unpack_from("<i", data, offset + 124)[0],
            "sequence_group": struct.unpack_from("<i", data, offset + 156)[0],
        })

    textures = []
    for index in _range(data, header["texture_offset"], header["texture_count"], 80, "texture"):
        offset = header["texture_offset"] + index * 80
        flags, width, height, pixel_offset = struct.unpack_from("<4i", data, offset + 64)
        textures.append({"index": index, "record_offset": offset, "name": _cstring(data, offset, 64), "flags": flags, "flag_names": [name for name, value in TEXTURE_FLAGS.items() if flags & value], "width": width, "height": height, "pixel_offset": pixel_offset})

    skin_families = []
    skin_count = header["skin_ref_count"] * header["skin_family_count"]
    for _ in _range(data, header["skin_offset"], skin_count, 2, "skin"):
        pass
    for family in range(header["skin_family_count"]):
        offset = header["skin_offset"] + family * header["skin_ref_count"] * 2
        skin_families.append(list(struct.unpack_from(f"<{header['skin_ref_count']}h", data, offset)) if header["skin_ref_count"] else [])

    bodyparts = []
    for index in _range(data, header["bodypart_offset"], header["bodypart_count"], 76, "bodypart"):
        offset = header["bodypart_offset"] + index * 76
        model_count, base, model_offset = struct.unpack_from("<3i", data, offset + 64)
        models = []
        for model_index in _range(data, model_offset, model_count, 112, f"bodypart {index} model"):
            current = model_offset + model_index * 112
            model_type, radius, mesh_count, mesh_offset, vertex_count, vertex_bone_offset, vertex_offset, normal_count, normal_bone_offset, normal_offset, group_count, group_offset = struct.unpack_from("<if10i", data, current + 64)
            models.append({"name": _cstring(data, current, 64), "type": model_type, "radius": radius, "mesh_count": mesh_count, "mesh_offset": mesh_offset, "vertex_count": vertex_count, "vertex_bone_offset": vertex_bone_offset, "vertex_offset": vertex_offset, "normal_count": normal_count, "normal_bone_offset": normal_bone_offset, "normal_offset": normal_offset, "group_count": group_count, "group_offset": group_offset})
        bodyparts.append({"name": _cstring(data, offset, 64), "model_count": model_count, "base": base, "models": models})

    attachments = []
    for index in _range(data, header["attachment_offset"], header["attachment_count"], 88, "attachment"):
        offset = header["attachment_offset"] + index * 88
        attachment_type, bone = struct.unpack_from("<2i", data, offset + 32)
        attachments.append({"index": index, "name": _cstring(data, offset, 32), "type": attachment_type, "bone": bone, "origin": list(struct.unpack_from("<3f", data, offset + 40)), "vectors": [list(struct.unpack_from("<3f", data, offset + 52 + axis * 12)) for axis in range(3)]})

    return {"path": str(resolved), "magic": "IDST", "version": 10, "size": len(data), "header": header, "bones": bones, "controllers": controllers, "hitboxes": hitboxes, "sequences": sequences, "textures": textures, "skin_families": skin_families, "bodyparts": bodyparts, "attachments": attachments}


def _decode_animation_value(
    data: bytes,
    animation_record_offset: int,
    relative_offset: int,
    frame: int,
) -> int:
    """Decode one integer-frame mstudioanimvalue_t channel sample."""

    if frame < 0:
        raise MdlError("animation frame must be non-negative")
    cursor = animation_record_offset + relative_offset
    remaining = int(frame)
    while True:
        if cursor < 0 or cursor + 2 > len(data):
            raise MdlError("animation value header is outside the MDL")
        valid, total = struct.unpack_from("<BB", data, cursor)
        if total == 0 or valid > total:
            raise MdlError(f"invalid animation span: valid={valid}, total={total}")
        value_end = cursor + (valid + 1) * 2
        if value_end > len(data):
            raise MdlError("animation value span is outside the MDL")
        if remaining < total:
            value_index = remaining + 1 if remaining < valid else valid
            return int(struct.unpack_from("<h", data, cursor + value_index * 2)[0])
        remaining -= total
        cursor = value_end


def decode_mdl_sequence(
    path: Path | str,
    sequence_name: str,
    *,
    blend: int = 0,
) -> dict:
    """Decode integer local bone transforms from an embedded MDL v10 sequence."""

    resolved = Path(path).expanduser().resolve()
    data = resolved.read_bytes()
    inspection = inspect_mdl(resolved)
    sequence = next(
        (item for item in inspection["sequences"] if item["name"].casefold() == sequence_name.casefold()),
        None,
    )
    if sequence is None:
        raise MdlError(f"sequence not found in MDL: {sequence_name}")
    if sequence["sequence_group"] != 0:
        raise MdlError(f"external sequence groups are not supported: {sequence_name}")
    if not 0 <= blend < sequence["blend_count"]:
        raise MdlError(f"blend {blend} is outside sequence blend count {sequence['blend_count']}")
    bone_count = len(inspection["bones"])
    animation_base = int(sequence["animation_offset"])
    _range(data, animation_base, sequence["blend_count"] * bone_count, 12, "animation records")
    frames = []
    for frame in range(sequence["frame_count"]):
        poses = []
        for bone_index, bone in enumerate(inspection["bones"]):
            record_offset = animation_base + (blend * bone_count + bone_index) * 12
            offsets = struct.unpack_from("<6H", data, record_offset)
            values = []
            for channel, relative_offset in enumerate(offsets):
                raw = 0 if relative_offset == 0 else _decode_animation_value(
                    data,
                    record_offset,
                    int(relative_offset),
                    frame,
                )
                values.append(float(bone["values"][channel]) + raw * float(bone["scales"][channel]))
            poses.append({
                "bone": bone_index,
                "name": bone["name"],
                "position": values[:3],
                "rotation": values[3:],
            })
        frames.append(poses)
    return {
        "path": str(resolved),
        "sequence": sequence["name"],
        "fps": sequence["fps"],
        "frame_count": sequence["frame_count"],
        "blend": blend,
        "frames": frames,
    }


def compare_mdl_sequence_to_smd(
    mdl_path: Path | str,
    smd_path: Path | str,
    sequence_name: str,
    *,
    position_tolerance: float = 0.02,
    rotation_tolerance: float = 0.002,
    root_z_rotation: float = pi / 2.0,
    smd_scale: float = 1.0,
) -> dict:
    """Compare compiled MDL animation channels with their source animation SMD."""

    from .smd import read_smd, validate_smd

    decoded = decode_mdl_sequence(mdl_path, sequence_name)
    smd = read_smd(smd_path)
    issues = validate_smd(smd, require_triangles=False)
    smd_bones = {
        bone.name: bone.index
        for bone in smd.bones
        if bone.name.casefold() != "blender_implicit"
    }
    root_names = {
        bone.name
        for bone in smd.bones
        if bone.parent == -1 and bone.name.casefold() != "blender_implicit"
    }
    decoded_names = [pose["name"] for pose in decoded["frames"][0]] if decoded["frames"] else []
    if set(decoded_names) != set(smd_bones):
        issues.append("compiled MDL and animation SMD bone maps differ")
    if decoded["frame_count"] != len(smd.frames):
        issues.append(
            f"compiled MDL frame count {decoded['frame_count']} differs from SMD {len(smd.frames)}"
        )
    smd_frames = list(smd.frames.items())
    frame_count = min(decoded["frame_count"], len(smd_frames))
    worst_position = {"error": 0.0, "frame": None, "bone": None}
    worst_rotation = {
        "error": 0.0, "frame": None, "compiled_frame": None,
        "bone": None, "axis": None, "axis_channel_error": 0.0,
    }
    for compiled_frame in range(frame_count):
        smd_frame, source_poses = smd_frames[compiled_frame]
        smd_poses = {pose.bone: pose for pose in source_poses}
        for compiled_pose in decoded["frames"][compiled_frame]:
            bone_name = compiled_pose["name"]
            bone_id = smd_bones.get(bone_name)
            if bone_id is None or bone_id not in smd_poses:
                continue
            smd_pose = smd_poses[bone_id]
            expected_position = list(smd_pose.position)
            expected_rotation = list(smd_pose.rotation)
            if bone_name in root_names:
                x, y = expected_position[:2]
                expected_position[0] = cos(root_z_rotation) * x - sin(root_z_rotation) * y
                expected_position[1] = sin(root_z_rotation) * x + cos(root_z_rotation) * y
                expected_rotation[2] += root_z_rotation
            expected_position = [float(value) * float(smd_scale) for value in expected_position]
            position_error = sqrt(sum(
                (float(left) - float(right)) ** 2
                for left, right in zip(compiled_pose["position"], expected_position)
            ))
            if position_error > worst_position["error"]:
                worst_position = {
                    "error": position_error,
                    "frame": smd_frame,
                    "compiled_frame": compiled_frame,
                    "bone": bone_name,
                }
            axis_errors = [
                abs(atan2(sin(float(left) - float(right)), cos(float(left) - float(right))))
                for left, right in zip(compiled_pose["rotation"], expected_rotation)
            ]
            rotation_error = euler_xyz_rotation_error(compiled_pose["rotation"], expected_rotation)
            if rotation_error > worst_rotation["error"]:
                axis = max(range(3), key=axis_errors.__getitem__)
                worst_rotation = {
                    "error": rotation_error,
                    "frame": smd_frame,
                    "compiled_frame": compiled_frame,
                    "bone": bone_name,
                    "axis": axis,
                    "axis_channel_error": axis_errors[axis],
                }
    if worst_position["error"] > position_tolerance:
        issues.append(
            "compiled MDL position channels diverge from animation SMD: "
            f"{worst_position['error']:.6f} > {position_tolerance:.6f}"
        )
    if worst_rotation["error"] > rotation_tolerance:
        issues.append(
            "compiled MDL rotation channels diverge from animation SMD: "
            f"{worst_rotation['error']:.6f} > {rotation_tolerance:.6f}"
        )
    return {
        "status": "pass" if not issues else "fail",
        "issues": list(dict.fromkeys(issues)),
        "method": "MDL v10 mstudioanimvalue_t decode compared in SMD declaration order; positions use local channels and rotations use local XYZ matrices after the StudioMDL root +90 degree convention and QC scale",
        "sequence": sequence_name,
        "frames_checked": frame_count,
        "position_tolerance": float(position_tolerance),
        "rotation_tolerance": float(rotation_tolerance),
        "max_position_error": float(worst_position["error"]),
        "max_rotation_error": float(worst_rotation["error"]),
        "worst_position": worst_position,
        "worst_rotation": worst_rotation,
    }


def patch_texture_flags(
    path: Path | str,
    texture_modes: dict[str, Iterable[str] | int],
    *,
    output: Path | str | None = None,
    preserve_unknown: bool = True,
) -> dict:
    source = Path(path).expanduser().resolve()
    inspection = inspect_mdl(source)
    requested = {name.casefold(): value for name, value in texture_modes.items()}
    data = bytearray(source.read_bytes())
    patched = []
    found: set[str] = set()
    for texture in inspection["textures"]:
        key = texture["name"].casefold()
        if key not in requested:
            continue
        value = requested[key]
        flags = value if isinstance(value, int) else sum(TEXTURE_FLAGS[mode.casefold()] for mode in value)
        if preserve_unknown:
            flags |= texture["flags"] & ~KNOWN_TEXTURE_MASK
        struct.pack_into("<i", data, texture["record_offset"] + 64, flags)
        patched.append({"name": texture["name"], "before": texture["flags"], "after": flags})
        found.add(key)
    missing = sorted(set(requested) - found)
    if missing:
        raise MdlError(f"textures not found in MDL: {', '.join(missing)}")
    destination = Path(output).expanduser().resolve() if output else source
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(destination)
    return {"path": str(destination), "patched": patched, "inspection": inspect_mdl(destination)}


def validate_mdl_contract(inspection: dict, contract: dict, *, tolerance: float = 0.01) -> list[dict]:
    """Compare parsed binary tables with a normalized model contract."""
    from .model_contract import effective_texture_modes

    issues: list[dict] = []

    def fail(code: str, message: str, **context) -> None:
        issues.append({"severity": "error", "code": code, "message": message, "context": context})

    def vectors_match(left, right) -> bool:
        return len(left) == len(right) and all(
            abs(float(first) - float(second)) <= tolerance
            for first, second in zip(left, right)
        )

    header = inspection["header"]
    expected_bones = [(item["name"], item.get("parent")) for item in contract["bones"]]
    actual_bones = []
    for bone in inspection["bones"]:
        parent = inspection["bones"][bone["parent"]]["name"] if bone["parent"] >= 0 else None
        actual_bones.append((bone["name"], parent))
    if [(name.casefold(), parent.casefold() if parent else None) for name, parent in actual_bones] != [(name.casefold(), parent.casefold() if parent else None) for name, parent in expected_bones]:
        fail("mdl.bones", "compiled bone graph differs from contract", expected=expected_bones, actual=actual_bones)

    expected_bodyparts = []
    for body in contract["bodies"]:
        sources = body.get("_compiled_sources") or [body["source"]]
        for index, _source in enumerate(sources):
            name = body["name"] if len(sources) == 1 else f"{body['name']}_part{index + 1:03d}"
            expected_bodyparts.append((name, 1))
    expected_bodyparts.extend((group["name"], len(group["choices"])) for group in contract["bodygroups"])
    actual_bodyparts = [(item["name"], item["model_count"]) for item in inspection["bodyparts"]]
    if [(a.casefold(), b) for a, b in actual_bodyparts] != [(a.casefold(), b) for a, b in expected_bodyparts]:
        fail("mdl.bodyparts", "compiled bodyparts differ from contract", expected=expected_bodyparts, actual=actual_bodyparts)

    texture_names = [texture["name"] for texture in inspection["textures"]]
    if len(texture_names) > 64:
        fail(
            "mdl.texture_budget", "compiled MDL exceeds the complete texture-table budget",
            expected_maximum=64, actual=len(texture_names),
        )
    actual_families = [[texture_names[index] if 0 <= index < len(texture_names) else f"<invalid:{index}>" for index in row] for row in inspection["skin_families"]]
    expected_families = contract["skin_families"] or ([texture_names] if texture_names else [])
    if [[name.casefold() for name in row] for row in actual_families] != [[name.casefold() for name in row] for row in expected_families]:
        fail("mdl.skins", "compiled skin-family table differs from contract", expected=expected_families, actual=actual_families)

    texture_by_name = {item["name"].casefold(): item for item in inspection["textures"]}
    for texture in contract["textures"]:
        actual = texture_by_name.get(texture["name"].casefold())
        if actual is None:
            fail("mdl.texture_missing", f"compiled texture is missing: {texture['name']}")
            continue
        if (actual["width"], actual["height"]) != (texture["width"], texture["height"]):
            fail("mdl.texture_dimensions", f"compiled texture dimensions differ: {texture['name']}", expected=[texture["width"], texture["height"]], actual=[actual["width"], actual["height"]])
        expected_flags = sum(TEXTURE_FLAGS[mode] for mode in effective_texture_modes(texture))
        if actual["flags"] & expected_flags != expected_flags:
            fail("mdl.texture_flags", f"compiled texture flags are missing: {texture['name']}", expected=expected_flags, actual=actual["flags"])

    sequence_by_name = {item["name"].casefold(): item for item in inspection["sequences"]}
    for sequence in contract["sequences"]:
        actual = sequence_by_name.get(sequence["name"].casefold())
        if actual is None:
            fail("mdl.sequence_missing", f"compiled sequence is missing: {sequence['name']}")
            continue
        if abs(actual["fps"] - float(sequence["fps"])) > tolerance:
            fail("mdl.sequence_fps", f"sequence FPS differs: {sequence['name']}", expected=sequence["fps"], actual=actual["fps"])
        if bool(actual["loop"]) != bool(sequence.get("loop")):
            fail("mdl.sequence_loop", f"sequence loop flag differs: {sequence['name']}")
        frame_range = sequence.get("frame")
        if frame_range is not None:
            expected_frames = int(frame_range[1]) - int(frame_range[0]) + 1
            if actual["frame_count"] != expected_frames:
                fail(
                    "mdl.sequence_frames", f"sequence frame count differs: {sequence['name']}",
                    expected=expected_frames, actual=actual["frame_count"],
                )
        activity = sequence.get("activity")
        if isinstance(activity, dict):
            expected_activity = ACTIVITY_IDS.get(str(activity.get("name", "")).casefold())
            expected_weight = int(activity.get("weight", 1))
            if expected_activity is None:
                fail(
                    "mdl.sequence_activity_contract",
                    f"sequence activity is not a known GoldSrc activity: {sequence['name']}",
                    activity=activity.get("name"),
                )
            elif (actual["activity"], actual["activity_weight"]) != (
                expected_activity, expected_weight,
            ):
                fail(
                    "mdl.sequence_activity", f"sequence activity differs: {sequence['name']}",
                    expected={"activity": expected_activity, "weight": expected_weight},
                    actual={"activity": actual["activity"], "weight": actual["activity_weight"]},
                )
        elif (actual["activity"], actual["activity_weight"]) != (0, 0):
            fail(
                "mdl.sequence_activity", f"sequence has an undeclared activity: {sequence['name']}",
                expected={"activity": 0, "weight": 0},
                actual={"activity": actual["activity"], "weight": actual["activity_weight"]},
            )
        expected_motion = sum(MOTION_TYPES[axis] for axis in sequence.get("motion", []))
        if actual["motion_type"] != expected_motion:
            fail(
                "mdl.sequence_motion", f"sequence motion flags differ: {sequence['name']}",
                expected=expected_motion, actual=actual["motion_type"],
            )
        if not expected_motion and not vectors_match(actual["linear_movement"], [0.0, 0.0, 0.0]):
            fail(
                "mdl.sequence_linear_movement",
                f"sequence has undeclared linear movement: {sequence['name']}",
                expected=[0.0, 0.0, 0.0], actual=actual["linear_movement"],
            )
        expected_events = [(item["frame"], item["id"], item.get("options", "")) for item in sequence.get("events", [])]
        actual_events = [(item["frame"], item["id"], item.get("options", "")) for item in actual["events"]]
        if actual_events != expected_events:
            fail("mdl.sequence_events", f"sequence events differ: {sequence['name']}", expected=expected_events, actual=actual_events)

    bone_indices = {bone["name"].casefold(): bone["index"] for bone in inspection["bones"]}
    expected_controllers = [
        {
            "bone": bone_indices.get(item["bone"].casefold()),
            "type": CONTROLLER_TYPES[item["type"]],
            "start": float(item["start"]),
            "end": float(item["end"]),
            "index": int(item["index"]),
        }
        for item in sorted(contract["controllers"], key=lambda value: int(value["index"]))
    ]
    actual_controllers = sorted(inspection["controllers"], key=lambda value: int(value["index"]))
    controllers_match = len(actual_controllers) == len(expected_controllers)
    if controllers_match:
        for expected, actual in zip(expected_controllers, actual_controllers):
            controllers_match = (
                expected["bone"] == actual["bone"]
                and expected["type"] == actual["type"]
                and expected["index"] == actual["index"]
                and abs(expected["start"] - float(actual["start"])) <= tolerance
                and abs(expected["end"] - float(actual["end"])) <= tolerance
            )
            if not controllers_match:
                break
    if not controllers_match:
        fail(
            "mdl.controllers", "compiled controllers differ from contract",
            expected=expected_controllers, actual=actual_controllers,
        )
    # An empty contract list means no hitbox shape was requested. GoldSrc
    # StudioMDL may then generate a compiler-owned subset from skinned bones.
    if contract["hitboxes"]:
        expected_hitboxes = [
            {
                "bone": bone_indices.get(item["bone"].casefold()),
                "group": int(item.get("group", 0)),
                "min": [float(value) for value in item["min"]],
                "max": [float(value) for value in item["max"]],
            }
            for item in contract["hitboxes"]
        ]
        actual_hitboxes = inspection["hitboxes"]
        hitboxes_match = len(actual_hitboxes) == len(expected_hitboxes) and all(
            expected["bone"] == actual["bone"]
            and expected["group"] == actual["group"]
            and vectors_match(expected["min"], actual["min"])
            and vectors_match(expected["max"], actual["max"])
            for expected, actual in zip(expected_hitboxes, actual_hitboxes)
        )
        if not hitboxes_match:
            fail(
                "mdl.hitboxes", "compiled hitboxes differ from contract",
                expected=expected_hitboxes, actual=actual_hitboxes,
            )
    expected_attachments = [
        {
            "index": int(item["index"]),
            "bone": bone_indices.get(item["bone"].casefold()),
            "origin": [float(value) for value in item["origin"]],
        }
        for item in sorted(contract["attachments"], key=lambda value: int(value["index"]))
    ]
    actual_attachments = sorted(inspection["attachments"], key=lambda value: int(value["index"]))
    attachments_match = len(actual_attachments) == len(expected_attachments) and all(
        expected["index"] == actual["index"]
        and expected["bone"] == actual["bone"]
        and vectors_match(expected["origin"], actual["origin"])
        for expected, actual in zip(expected_attachments, actual_attachments)
    )
    if not attachments_match:
        fail(
            "mdl.attachments", "compiled attachments differ from contract",
            expected=expected_attachments, actual=actual_attachments,
        )

    for kind, header_min, header_max in (("bbox", "min", "max"), ("cbox", "bbmin", "bbmax")):
        expected = [*contract["bounds"][kind]["min"], *contract["bounds"][kind]["max"]]
        actual = [*header[header_min], *header[header_max]]
        if any(abs(float(left) - float(right)) > tolerance for left, right in zip(expected, actual)):
            fail(f"mdl.{kind}", f"compiled {kind} differs from contract", expected=expected, actual=actual)
    return issues
