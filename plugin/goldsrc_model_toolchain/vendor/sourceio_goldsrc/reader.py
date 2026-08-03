"""Minimal SourceIO-derived GoldSrc MDL v10 reader.

This module intentionally does not import the project-owned ``mdl_v10``
inspector.  It is the independent parser used by round-trip acceptance.
Derived from SourceIO 5.5.4 (MIT), Copyright (c) 2020 REDxEYE.
"""

from __future__ import annotations

import contextlib
import io
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np


class Buffer:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def tell(self) -> int:
        return self.offset

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> None:
        if whence == io.SEEK_SET:
            target = offset
        elif whence == io.SEEK_CUR:
            target = self.offset + offset
        elif whence == io.SEEK_END:
            target = len(self.data) + offset
        else:
            raise ValueError(f"unsupported seek mode: {whence}")
        if not 0 <= target <= len(self.data):
            raise ValueError(f"MDL seek outside file: {target}")
        self.offset = target

    def read(self, size: int) -> bytes:
        end = self.offset + size
        if size < 0 or end > len(self.data):
            raise ValueError("MDL read outside file")
        value = self.data[self.offset:end]
        self.offset = end
        return value

    def unpack(self, fmt: str):
        layout = struct.Struct("<" + fmt)
        values = layout.unpack(self.read(layout.size))
        return values[0] if len(values) == 1 else values

    def text(self, size: int) -> str:
        return self.read(size).split(b"\0", 1)[0].decode("latin1", errors="replace")

    @contextlib.contextmanager
    def saved(self):
        current = self.offset
        try:
            yield
        finally:
            self.offset = current

    @contextlib.contextmanager
    def at(self, offset: int):
        with self.saved():
            self.seek(offset)
            yield


@dataclass(slots=True)
class Header:
    name: str
    file_size: int
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    cbox_min: tuple[float, float, float]
    cbox_max: tuple[float, float, float]
    flags: int
    bone_count: int
    bone_offset: int
    sequence_count: int
    sequence_offset: int
    sequence_group_count: int
    sequence_group_offset: int
    texture_count: int
    texture_offset: int
    skin_ref_count: int
    skin_family_count: int
    skin_offset: int
    bodypart_count: int
    bodypart_offset: int

    @classmethod
    def read(cls, buffer: Buffer) -> "Header":
        if buffer.read(4) != b"IDST":
            raise ValueError("not a GoldSrc IDST model")
        version = buffer.unpack("i")
        if version != 10:
            raise ValueError(f"unsupported MDL version: {version}")
        name = buffer.text(64)
        file_size = buffer.unpack("i")
        _eye = buffer.unpack("3f")
        bbox_min = buffer.unpack("3f")
        bbox_max = buffer.unpack("3f")
        cbox_min = buffer.unpack("3f")
        cbox_max = buffer.unpack("3f")
        flags = buffer.unpack("i")
        values = buffer.unpack("26I")
        return cls(
            name, file_size, bbox_min, bbox_max, cbox_min, cbox_max, flags,
            values[0], values[1], values[6], values[7], values[8], values[9],
            values[10], values[11], values[13], values[14], values[15],
            values[16], values[17],
        )


@dataclass(slots=True)
class Bone:
    name: str
    parent: int
    flags: int
    controllers: tuple[int, ...]
    position: tuple[float, float, float]
    rotation: tuple[float, float, float]
    position_scale: tuple[float, float, float]
    rotation_scale: tuple[float, float, float]

    @classmethod
    def read(cls, buffer: Buffer) -> "Bone":
        return cls(
            buffer.text(32), buffer.unpack("i"), buffer.unpack("i"), buffer.unpack("6i"),
            buffer.unpack("3f"), buffer.unpack("3f"), buffer.unpack("3f"), buffer.unpack("3f"),
        )


@dataclass(slots=True)
class SequenceEvent:
    frame: int
    event: int
    event_type: int
    options: str


@dataclass(slots=True)
class Sequence:
    entry_offset: int
    name: str
    fps: float
    flags: int
    activity: int
    activity_weight: int
    events: list[SequenceEvent]
    frame_count: int
    motion_type: int
    motion_bone: int
    linear_movement: tuple[float, float, float]
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    blend_count: int
    animation_offset: int
    blend_type: tuple[int, int]
    blend_start: tuple[float, float]
    blend_end: tuple[float, float]
    blend_parent: int
    sequence_group: int
    entry_node: int
    exit_node: int
    node_flags: int
    next_sequence: int

    @classmethod
    def read(cls, buffer: Buffer) -> "Sequence":
        entry_offset = buffer.tell()
        name = buffer.text(32)
        fps = buffer.unpack("f")
        flags, activity, weight, event_count, event_offset = buffer.unpack("5i")
        frame_count, _pivot_count, _pivot_offset, motion_type, motion_bone = buffer.unpack("5i")
        linear_movement = buffer.unpack("3f")
        buffer.read(8)
        bbox_min = buffer.unpack("3f")
        bbox_max = buffer.unpack("3f")
        blend_count, animation_offset = buffer.unpack("2i")
        blend_type = buffer.unpack("2i")
        blend_start = buffer.unpack("2f")
        blend_end = buffer.unpack("2f")
        blend_parent, sequence_group, entry, exit_node, node_flags, next_sequence = buffer.unpack("6i")
        events = []
        if event_count:
            with buffer.at(event_offset):
                events = [
                    SequenceEvent(
                        buffer.unpack("i"), buffer.unpack("i"), buffer.unpack("i"), buffer.text(64),
                    )
                    for _index in range(event_count)
                ]
        return cls(
            entry_offset, name, fps, flags, activity, weight, events, frame_count,
            motion_type, motion_bone, linear_movement, bbox_min, bbox_max,
            blend_count, animation_offset, blend_type, blend_start, blend_end,
            blend_parent, sequence_group, entry, exit_node, node_flags, next_sequence,
        )

    @staticmethod
    def _channel(
        buffer: Buffer,
        record_offset: int,
        relative_offset: int,
        frame_count: int,
        base: float,
        scale: float,
    ) -> list[float]:
        if not relative_offset:
            return [base] * frame_count
        values: list[int] = []
        with buffer.at(record_offset + relative_offset):
            while len(values) < frame_count:
                valid, total = buffer.unpack("2B")
                if not total or valid > total:
                    raise ValueError(f"invalid animation span valid={valid} total={total}")
                if valid:
                    decoded = buffer.unpack(f"{valid}h")
                    values.extend([decoded] if valid == 1 else decoded)
                if total > valid:
                    if not values:
                        raise ValueError("animation span repeats without a value")
                    values.extend([values[-1]] * (total - valid))
        return [base + value * scale for value in values[:frame_count]]

    def animations(self, buffer: Buffer, bones: list[Bone]):
        if self.frame_count < 1 or self.blend_count < 1 or self.sequence_group != 0:
            return []
        blends = []
        for blend_index in range(self.blend_count):
            bone_channels = []
            for bone_index, bone in enumerate(bones):
                record_offset = self.animation_offset + (blend_index * len(bones) + bone_index) * 12
                with buffer.at(record_offset):
                    offsets = buffer.unpack("6H")
                channels = []
                for channel, relative in enumerate(offsets):
                    base = bone.position[channel] if channel < 3 else bone.rotation[channel - 3]
                    scale = bone.position_scale[channel] if channel < 3 else bone.rotation_scale[channel - 3]
                    channels.append(self._channel(buffer, record_offset, relative, self.frame_count, base, scale))
                bone_channels.append(channels)
            frames = []
            for frame in range(self.frame_count):
                frames.append([
                    (
                        tuple(channels[channel][frame] for channel in range(3)),
                        tuple(channels[channel][frame] for channel in range(3, 6)),
                    )
                    for channels in bone_channels
                ])
            blends.append(frames)
        return blends


@dataclass(slots=True)
class TriVert:
    vertex: int
    normal: int
    uv: tuple[int, int]


@dataclass(slots=True)
class Mesh:
    triangle_count: int
    skin_ref: int
    commands: list[tuple[list[TriVert], bool]]

    @classmethod
    def read(cls, buffer: Buffer) -> "Mesh":
        triangle_count, command_offset, skin_ref, _normal_count, _normal_offset = buffer.unpack("5i")
        commands = []
        with buffer.at(command_offset):
            while True:
                count = buffer.unpack("h")
                if count == 0:
                    break
                fan = count < 0
                vertices = []
                for _index in range(abs(count)):
                    vertex, normal, u, v = buffer.unpack("2H2h")
                    vertices.append(TriVert(vertex, normal, (u, v)))
                commands.append((vertices, fan))
        return cls(triangle_count, skin_ref, commands)


@dataclass(slots=True)
class Model:
    name: str
    bone_vertices: np.ndarray
    bone_normals: np.ndarray
    meshes: list[Mesh]
    vertices: np.ndarray
    normals: np.ndarray

    @classmethod
    def read(cls, buffer: Buffer) -> "Model":
        name = buffer.text(64)
        (
            _model_type, _radius, mesh_count, mesh_offset, vertex_count,
            vertex_bone_offset, vertex_offset, normal_count, normal_bone_offset,
            normal_offset, _group_count, _group_offset,
        ) = buffer.unpack("if10i")
        with buffer.saved():
            buffer.seek(mesh_offset)
            meshes = [Mesh.read(buffer) for _index in range(mesh_count)]
            buffer.seek(vertex_bone_offset)
            bone_vertices = np.frombuffer(buffer.read(vertex_count), dtype=np.uint8).copy()
            buffer.seek(vertex_offset)
            vertices = np.frombuffer(buffer.read(vertex_count * 12), dtype="<f4").reshape((-1, 3)).copy()
            buffer.seek(normal_bone_offset)
            bone_normals = np.frombuffer(buffer.read(normal_count), dtype=np.uint8).copy()
            buffer.seek(normal_offset)
            normals = np.frombuffer(buffer.read(normal_count * 12), dtype="<f4").reshape((-1, 3)).copy()
        return cls(name, bone_vertices, bone_normals, meshes, vertices, normals)


@dataclass(slots=True)
class Bodypart:
    name: str
    base: int
    models: list[Model]

    @classmethod
    def read(cls, buffer: Buffer) -> "Bodypart":
        name = buffer.text(64)
        count, base, offset = buffer.unpack("3i")
        with buffer.at(offset):
            models = [Model.read(buffer) for _index in range(count)]
        return cls(name, base, models)


@dataclass(slots=True)
class Texture:
    name: str
    flags: int
    width: int
    height: int
    indices: np.ndarray
    palette: np.ndarray
    pixels: np.ndarray

    @classmethod
    def read(cls, buffer: Buffer) -> "Texture":
        name = buffer.text(64)
        flags, width, height, offset = buffer.unpack("4I")
        if not width or not height or width > 4096 or height > 4096:
            raise ValueError(f"invalid embedded texture dimensions for {name}: {width}x{height}")
        with buffer.at(offset):
            indices = np.frombuffer(buffer.read(width * height), dtype=np.uint8).copy()
            palette = np.frombuffer(buffer.read(256 * 3), dtype=np.uint8).reshape((-1, 3)).copy()
            rgba_palette = np.concatenate((palette, np.full((256, 1), 255, dtype=np.uint8)), axis=1)
            rgba = rgba_palette[indices].reshape((height, width, 4))
            if "{" in name:
                rgba[indices.reshape((height, width)) == 255, 3] = 0
            rgba = np.flip(rgba, axis=0).copy()
        return cls(
            name, flags, width, height,
            indices.reshape((height, width)), palette,
            rgba.astype(np.float32) / 255.0,
        )


@dataclass(slots=True)
class Mdl:
    header: Header
    bones: list[Bone]
    sequences: list[Sequence]
    animations: list
    bodyparts: list[Bodypart]
    textures: list[Texture]
    skin_families: list[list[int]]

    @classmethod
    def from_bytes(cls, data: bytes) -> "Mdl":
        buffer = Buffer(data)
        header = Header.read(buffer)
        if header.file_size and header.file_size > len(data):
            raise ValueError("MDL header file size exceeds input")
        with buffer.at(header.bone_offset):
            bones = [Bone.read(buffer) for _index in range(header.bone_count)]
        with buffer.at(header.sequence_offset):
            sequences = [Sequence.read(buffer) for _index in range(header.sequence_count)]
        animations = [sequence.animations(buffer, bones) for sequence in sequences]
        with buffer.at(header.bodypart_offset):
            bodyparts = [Bodypart.read(buffer) for _index in range(header.bodypart_count)]
        with buffer.at(header.texture_offset):
            textures = [Texture.read(buffer) for _index in range(header.texture_count)]
        skin_families = []
        if header.skin_ref_count and header.skin_family_count:
            with buffer.at(header.skin_offset):
                for _family in range(header.skin_family_count):
                    row = buffer.unpack(f"{header.skin_ref_count}H")
                    skin_families.append([row] if header.skin_ref_count == 1 else list(row))
        return cls(header, bones, sequences, animations, bodyparts, textures, skin_families)


def read_mdl(path: str | Path) -> Mdl:
    return Mdl.from_bytes(Path(path).expanduser().resolve().read_bytes())
