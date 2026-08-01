"""Parse and validate ASCII SMD version 1 files without Blender dependencies."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


class SmdError(ValueError):
    """Raised when an SMD is structurally invalid or unsafe for GoldSrc."""


GOLDSRC_MAX_MODEL_VERTICES = 2048
GOLDSRC_MAX_MODEL_TRIANGLES = 20000


@dataclass(frozen=True)
class SmdBone:
    index: int
    name: str
    parent: int


@dataclass(frozen=True)
class SmdPose:
    bone: int
    position: tuple[float, float, float]
    rotation: tuple[float, float, float]


@dataclass(frozen=True)
class SmdVertex:
    bone: int
    position: tuple[float, float, float]
    normal: tuple[float, float, float]
    uv: tuple[float, float]
    links: tuple[tuple[int, float], ...] = ()

    @property
    def influences(self) -> tuple[int, ...]:
        values = [self.bone] if self.bone >= 0 else []
        values.extend(bone for bone, weight in self.links if weight > 0.000001)
        return tuple(dict.fromkeys(values))


@dataclass(frozen=True)
class SmdTriangle:
    material: str
    vertices: tuple[SmdVertex, SmdVertex, SmdVertex]


@dataclass
class SmdDocument:
    path: Path | None
    bones: list[SmdBone] = field(default_factory=list)
    frames: dict[int, list[SmdPose]] = field(default_factory=dict)
    triangles: list[SmdTriangle] = field(default_factory=list)

    @property
    def materials(self) -> list[str]:
        return list(dict.fromkeys(triangle.material for triangle in self.triangles))

    def bounds(self) -> dict[str, list[float]] | None:
        points = [vertex.position for triangle in self.triangles for vertex in triangle.vertices]
        if not points:
            return None
        return {
            "min": [min(point[axis] for point in points) for axis in range(3)],
            "max": [max(point[axis] for point in points) for axis in range(3)],
        }


def compiled_model_vertex_count(document: SmdDocument) -> int:
    """Count the position/bone vertices StudioMDL stores in mstudiomodel_t."""

    return len({
        (vertex.bone, *vertex.position)
        for triangle in document.triangles
        for vertex in triangle.vertices
    })


def geometry_budget(document: SmdDocument, *, target_profile: str) -> dict:
    vertices = compiled_model_vertex_count(document)
    triangles = len(document.triangles)
    legacy_compatible = (
        vertices <= GOLDSRC_MAX_MODEL_VERTICES
        and triangles <= GOLDSRC_MAX_MODEL_TRIANGLES
    )
    return {
        "compiled_vertices": vertices,
        "triangles": triangles,
        "vertex_limit": GOLDSRC_MAX_MODEL_VERTICES,
        "triangle_limit": GOLDSRC_MAX_MODEL_TRIANGLES,
        "legacy_compatible": legacy_compatible,
        "hard_failure": target_profile == "half-life-cs" and not legacy_compatible,
    }


def animation_budget_hint(document: SmdDocument, *, budget: int = 65536) -> dict:
    """Estimate sequence density before StudioMDL's opaque 64K failure."""
    frame_count = len(document.frames)
    bone_count = len(document.bones)
    channel_density = frame_count * bone_count * 6
    return {
        "frames": frame_count,
        "bones": bone_count,
        "channel_density": channel_density,
        "budget": budget,
        "risk": channel_density > budget,
        "recommended_sample_step": max(1, math.ceil(channel_density / budget)),
        "note": "heuristic; StudioMDL animation compression remains authoritative",
    }

    def to_dict(self) -> dict:
        return {
            "path": str(self.path) if self.path else None,
            "bones": [{"index": bone.index, "name": bone.name, "parent": bone.parent} for bone in self.bones],
            "frames": {str(frame): len(poses) for frame, poses in sorted(self.frames.items())},
            "triangles": len(self.triangles),
            "vertices": len(self.triangles) * 3,
            "materials": self.materials,
            "bounds": self.bounds(),
        }


def _split_sections(lines: Iterable[str]) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        lowered = line.casefold()
        if current is None:
            if lowered == "version 1":
                continue
            if lowered in {"nodes", "skeleton", "triangles"}:
                current = lowered
                sections.setdefault(current, [])
                continue
            raise SmdError(f"unexpected SMD token outside a section: {line}")
        if lowered == "end":
            current = None
        else:
            sections[current].append(line)
    if current is not None:
        raise SmdError(f"unterminated SMD section: {current}")
    return sections


def _parse_nodes(lines: list[str]) -> list[SmdBone]:
    bones: list[SmdBone] = []
    for line in lines:
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or '"' not in parts[1]:
            raise SmdError(f"invalid node line: {line}")
        index = int(parts[0])
        quoted = parts[1]
        end = quoted.rfind('"')
        name = quoted[1:end] if quoted.startswith('"') and end > 0 else ""
        parent_text = quoted[end + 1 :].strip()
        if not name or not parent_text:
            raise SmdError(f"invalid node line: {line}")
        bones.append(SmdBone(index, name, int(parent_text)))
    return bones


def _parse_skeleton(lines: list[str]) -> dict[int, list[SmdPose]]:
    frames: dict[int, list[SmdPose]] = {}
    frame: int | None = None
    for line in lines:
        if line.casefold().startswith("time "):
            frame = int(line.split()[1])
            if frame in frames:
                raise SmdError(f"duplicate skeleton frame: {frame}")
            frames[frame] = []
            continue
        if frame is None:
            raise SmdError(f"pose before first time declaration: {line}")
        fields = line.split()
        if len(fields) != 7:
            raise SmdError(f"invalid skeleton pose: {line}")
        frames[frame].append(
            SmdPose(int(fields[0]), tuple(map(float, fields[1:4])), tuple(map(float, fields[4:7])))
        )
    return frames


def _parse_vertex(line: str) -> SmdVertex:
    fields = line.split()
    if len(fields) < 9:
        raise SmdError(f"invalid SMD vertex: {line}")
    links: list[tuple[int, float]] = []
    if len(fields) > 9:
        link_count = int(fields[9])
        if len(fields) != 10 + link_count * 2:
            raise SmdError(f"invalid weighted SMD vertex: {line}")
        links = [(int(fields[10 + i * 2]), float(fields[11 + i * 2])) for i in range(link_count)]
    return SmdVertex(
        int(fields[0]),
        tuple(map(float, fields[1:4])),
        tuple(map(float, fields[4:7])),
        tuple(map(float, fields[7:9])),
        tuple(links),
    )


def _parse_triangles(lines: list[str]) -> list[SmdTriangle]:
    if len(lines) % 4:
        raise SmdError("triangles section must contain one material line followed by three vertices")
    triangles = []
    for offset in range(0, len(lines), 4):
        material = lines[offset].strip().strip('"')
        if not material:
            raise SmdError("empty SMD material token")
        vertices = tuple(_parse_vertex(line) for line in lines[offset + 1 : offset + 4])
        triangles.append(SmdTriangle(material, vertices))
    return triangles


def parse_smd(text: str, *, path: Path | None = None) -> SmdDocument:
    meaningful = [line.strip() for line in text.lstrip("\ufeff").splitlines() if line.strip()]
    if not meaningful or meaningful[0].casefold() != "version 1":
        raise SmdError("SMD must start with version 1")
    sections = _split_sections(meaningful)
    if "nodes" not in sections or "skeleton" not in sections:
        raise SmdError("SMD requires nodes and skeleton sections")
    return SmdDocument(
        path=path,
        bones=_parse_nodes(sections["nodes"]),
        frames=_parse_skeleton(sections["skeleton"]),
        triangles=_parse_triangles(sections.get("triangles", [])),
    )


def read_smd(path: Path | str) -> SmdDocument:
    resolved = Path(path).expanduser().resolve()
    return parse_smd(resolved.read_text(encoding="utf-8-sig"), path=resolved)


def validate_smd(
    document: SmdDocument,
    *,
    require_triangles: bool = False,
    require_single_weight: bool = True,
    require_complete_frames: bool = True,
    require_bmp_materials: bool = True,
) -> list[str]:
    errors: list[str] = []
    ids = [bone.index for bone in document.bones]
    names = [bone.name.casefold() for bone in document.bones]
    if len(ids) != len(set(ids)):
        errors.append("duplicate bone ids")
    if len(names) != len(set(names)):
        errors.append("duplicate bone names")
    known = set(ids)
    for bone in document.bones:
        if bone.parent != -1 and bone.parent not in known:
            errors.append(f"bone {bone.name} references missing parent {bone.parent}")
    for bone in document.bones:
        seen = {bone.index}
        parent = bone.parent
        while parent != -1 and parent in known:
            if parent in seen:
                errors.append(f"bone cycle includes {bone.name}")
                break
            seen.add(parent)
            parent = next(item.parent for item in document.bones if item.index == parent)
    if require_complete_frames:
        for frame, poses in document.frames.items():
            pose_ids = [pose.bone for pose in poses]
            if set(pose_ids) != known or len(pose_ids) != len(known):
                errors.append(f"frame {frame} bone set does not match nodes")
    if require_triangles and not document.triangles:
        errors.append("reference SMD has no triangles")
    for triangle_index, triangle in enumerate(document.triangles):
        if require_bmp_materials and Path(triangle.material).suffix.casefold() != ".bmp":
            errors.append(f"triangle {triangle_index} material is not a BMP filename: {triangle.material}")
        for vertex_index, vertex in enumerate(triangle.vertices):
            if vertex.bone not in known:
                errors.append(f"triangle {triangle_index} vertex {vertex_index} has missing bone {vertex.bone}")
            if require_single_weight and len(vertex.influences) != 1:
                errors.append(
                    f"triangle {triangle_index} vertex {vertex_index} has {len(vertex.influences)} bone influences"
                )
            for linked_bone, _weight in vertex.links:
                if linked_bone not in known:
                    errors.append(f"triangle {triangle_index} vertex {vertex_index} links missing bone {linked_bone}")
    return list(dict.fromkeys(errors))
