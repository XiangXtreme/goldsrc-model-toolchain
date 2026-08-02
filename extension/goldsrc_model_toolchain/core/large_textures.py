"""GoldSrc texture-atlas tiling and deterministic SMD budget splitting."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .smd import SmdDocument, SmdTriangle, SmdVertex


GOLDSRC_TEXTURE_TILE_SIZE = 512
GOLDSRC_MAX_TEXTURES_PER_MODEL = 64
UV_EPSILON = 1.0 / GOLDSRC_TEXTURE_TILE_SIZE


class LargeTextureError(ValueError):
    """Raised when an atlas or its SMD mapping cannot be represented safely."""


@dataclass(frozen=True)
class LargeTextureResult:
    document: SmdDocument
    tiles: tuple[str, ...]
    original_triangles: int
    output_triangles: int
    crossed_triangles: int


def validate_large_texture_spec(spec: dict[str, Any]) -> dict[str, int | str]:
    name = spec.get("name")
    image = spec.get("image")
    width = spec.get("width")
    height = spec.get("height")
    tile_size = spec.get("tile_size", GOLDSRC_TEXTURE_TILE_SIZE)
    if not isinstance(name, str) or not name.lower().endswith(".bmp"):
        raise LargeTextureError("large texture name must end with .bmp")
    if not isinstance(image, str) or not image.strip():
        raise LargeTextureError(f"large texture {name} requires a Blender image name")
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in (width, height, tile_size)):
        raise LargeTextureError(f"large texture {name} dimensions must be integers")
    if width <= GOLDSRC_TEXTURE_TILE_SIZE and height <= GOLDSRC_TEXTURE_TILE_SIZE:
        raise LargeTextureError(f"large texture {name} must exceed 512 pixels on at least one axis")
    if tile_size != GOLDSRC_TEXTURE_TILE_SIZE:
        raise LargeTextureError("GoldSrc atlas tiles must be exactly 512x512")
    if width % tile_size or height % tile_size or width % 16 or height % 16:
        raise LargeTextureError(f"large texture {name} dimensions must be multiples of 512 and 16")
    tile_count = (width // tile_size) * (height // tile_size)
    if tile_count > GOLDSRC_MAX_TEXTURES_PER_MODEL:
        raise LargeTextureError(
            f"large texture {name} expands to {tile_count} tiles; one MDL supports at most "
            f"{GOLDSRC_MAX_TEXTURES_PER_MODEL} declared atlas tiles"
        )
    return {
        "name": name,
        "image": image,
        "width": width,
        "height": height,
        "tile_size": tile_size,
    }


def tile_counts(width: int, height: int, tile_size: int = GOLDSRC_TEXTURE_TILE_SIZE) -> tuple[int, int]:
    if tile_size <= 0 or width <= 0 or height <= 0:
        raise LargeTextureError("atlas dimensions must be positive")
    if width % tile_size or height % tile_size:
        raise LargeTextureError("atlas dimensions must divide evenly into GoldSrc tiles")
    return width // tile_size, height // tile_size


def tile_name(atlas_name: str, tile_x: int, tile_y: int) -> str:
    return f"{Path(atlas_name).stem}_{tile_x:02d}_{tile_y:02d}.bmp"


def tile_names(atlas_name: str, width: int, height: int, tile_size: int = GOLDSRC_TEXTURE_TILE_SIZE) -> list[str]:
    count_x, count_y = tile_counts(width, height, tile_size)
    return [
        tile_name(atlas_name, tile_x, tile_y)
        for tile_y in range(count_y)
        for tile_x in range(count_x)
    ]


def _lerp(left: float, right: float, factor: float) -> float:
    return left + (right - left) * factor


def _lerp_tuple(left: tuple[float, ...], right: tuple[float, ...], factor: float) -> tuple[float, ...]:
    return tuple(_lerp(a, b, factor) for a, b in zip(left, right))


def _normalize(value: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(sum(component * component for component in value))
    if length <= 1.0e-12:
        return value
    return tuple(component / length for component in value)


def _interpolate_vertex(left: SmdVertex, right: SmdVertex, factor: float) -> SmdVertex:
    if left.bone != right.bone or left.links != right.links:
        raise LargeTextureError(
            "a triangle crossing a large-texture tile has incompatible bone influences; "
            "split or bake the model with single-bone triangle influences first"
        )
    return SmdVertex(
        bone=left.bone,
        position=_lerp_tuple(left.position, right.position, factor),
        normal=_normalize(_lerp_tuple(left.normal, right.normal, factor)),
        uv=_lerp_tuple(left.uv, right.uv, factor),
        links=left.links,
    )


def _inside(vertex: SmdVertex, axis: int, boundary: float, keep_greater: bool) -> bool:
    value = vertex.uv[axis]
    return value >= boundary - 1.0e-12 if keep_greater else value <= boundary + 1.0e-12


def _intersection(left: SmdVertex, right: SmdVertex, axis: int, boundary: float) -> SmdVertex:
    denominator = right.uv[axis] - left.uv[axis]
    if abs(denominator) <= 1.0e-15:
        return left
    factor = (boundary - left.uv[axis]) / denominator
    return _interpolate_vertex(left, right, max(0.0, min(1.0, factor)))


def _clip_polygon(polygon: list[SmdVertex], axis: int, boundary: float, keep_greater: bool) -> list[SmdVertex]:
    if not polygon:
        return []
    result: list[SmdVertex] = []
    previous = polygon[-1]
    previous_inside = _inside(previous, axis, boundary, keep_greater)
    for current in polygon:
        current_inside = _inside(current, axis, boundary, keep_greater)
        if current_inside != previous_inside:
            result.append(_intersection(previous, current, axis, boundary))
        if current_inside:
            result.append(current)
        previous = current
        previous_inside = current_inside
    deduplicated: list[SmdVertex] = []
    for vertex in result:
        if deduplicated and all(abs(a - b) <= 1.0e-10 for a, b in zip(deduplicated[-1].uv, vertex.uv)):
            continue
        deduplicated.append(vertex)
    if len(deduplicated) > 1 and all(abs(a - b) <= 1.0e-10 for a, b in zip(deduplicated[0].uv, deduplicated[-1].uv)):
        deduplicated.pop()
    return deduplicated


def _cross(left: tuple[float, float, float], right: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _sub(left: tuple[float, float, float], right: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(a - b for a, b in zip(left, right))


def _dot(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _remap_uv(vertex: SmdVertex, tile_x: int, tile_y: int, width: int, height: int, tile_size: int, uv_clamp_factor: float) -> SmdVertex:
    width_uv = tile_size / width
    height_uv = tile_size / height
    local = (
        (vertex.uv[0] - tile_x * width_uv) / width_uv,
        (vertex.uv[1] - tile_y * height_uv) / height_uv,
    )
    local = tuple(max(uv_clamp_factor, min(1.0 - uv_clamp_factor, value)) for value in local)
    return SmdVertex(vertex.bone, vertex.position, vertex.normal, local, vertex.links)


def tile_smd_document(
    document: SmdDocument,
    *,
    atlas_name: str,
    width: int,
    height: int,
    tile_size: int = GOLDSRC_TEXTURE_TILE_SIZE,
    uv_clamp_factor: float = UV_EPSILON,
) -> LargeTextureResult:
    count_x, count_y = tile_counts(width, height, tile_size)
    atlas_key = atlas_name.casefold()
    output: list[SmdTriangle] = []
    used_tiles: set[str] = set()
    crossed = 0
    for triangle in document.triangles:
        if triangle.material.casefold() != atlas_key:
            output.append(triangle)
            continue
        if any(not math.isfinite(value) or value < -1.0e-8 or value > 1.0 + 1.0e-8 for vertex in triangle.vertices for value in vertex.uv):
            raise LargeTextureError(f"large texture UVs must stay within 0..1: {triangle.material}")
        minimum_u = min(vertex.uv[0] for vertex in triangle.vertices)
        maximum_u = max(vertex.uv[0] for vertex in triangle.vertices)
        minimum_v = min(vertex.uv[1] for vertex in triangle.vertices)
        maximum_v = max(vertex.uv[1] for vertex in triangle.vertices)
        minimum_x = max(0, min(count_x - 1, int(math.floor(max(0.0, minimum_u * count_x - 1.0e-12)))))
        maximum_x = max(0, min(count_x - 1, int(math.floor(maximum_u * count_x - 1.0e-12))))
        minimum_y = max(0, min(count_y - 1, int(math.floor(max(0.0, minimum_v * count_y - 1.0e-12)))))
        maximum_y = max(0, min(count_y - 1, int(math.floor(maximum_v * count_y - 1.0e-12))))
        if minimum_x != maximum_x or minimum_y != maximum_y:
            crossed += 1
        original_cross = _cross(
            _sub(triangle.vertices[1].position, triangle.vertices[0].position),
            _sub(triangle.vertices[2].position, triangle.vertices[0].position),
        )
        for tile_y in range(minimum_y, maximum_y + 1):
            for tile_x in range(minimum_x, maximum_x + 1):
                width_uv = tile_size / width
                height_uv = tile_size / height
                polygon = list(triangle.vertices)
                polygon = _clip_polygon(polygon, 0, tile_x * width_uv, True)
                polygon = _clip_polygon(polygon, 0, (tile_x + 1) * width_uv, False)
                polygon = _clip_polygon(polygon, 1, tile_y * height_uv, True)
                polygon = _clip_polygon(polygon, 1, (tile_y + 1) * height_uv, False)
                if len(polygon) < 3:
                    continue
                material = tile_name(atlas_name, tile_x, tile_y)
                used_tiles.add(material)
                remapped = [_remap_uv(vertex, tile_x, tile_y, width, height, tile_size, uv_clamp_factor) for vertex in polygon]
                for index in range(1, len(remapped) - 1):
                    candidate = [remapped[0], remapped[index], remapped[index + 1]]
                    candidate_cross = _cross(
                        _sub(candidate[1].position, candidate[0].position),
                        _sub(candidate[2].position, candidate[0].position),
                    )
                    if _dot(candidate_cross, original_cross) < 0.0:
                        candidate[1], candidate[2] = candidate[2], candidate[1]
                    if _dot(candidate_cross, candidate_cross) <= 1.0e-18:
                        continue
                    output.append(SmdTriangle(material, tuple(candidate)))
    return LargeTextureResult(
        document=SmdDocument(document.path, list(document.bones), dict(document.frames), output),
        tiles=tuple(sorted(used_tiles)),
        original_triangles=len(document.triangles),
        output_triangles=len(output),
        crossed_triangles=crossed,
    )


def split_smd_document(
    document: SmdDocument,
    *,
    max_vertices: int = 2048,
    max_normals: int = 2048,
    max_triangles: int = 20000,
) -> list[SmdDocument]:
    if min(max_vertices, max_normals, max_triangles) <= 0:
        raise LargeTextureError("SMD budgets must be positive")
    parts: list[SmdDocument] = []
    current: list[SmdTriangle] = []
    vertices: set[tuple[Any, ...]] = set()
    normals: set[tuple[Any, ...]] = set()

    def flush() -> None:
        if current:
            parts.append(SmdDocument(document.path, list(document.bones), dict(document.frames), list(current)))

    for triangle in document.triangles:
        triangle_vertices = {(vertex.bone, *vertex.position) for vertex in triangle.vertices}
        triangle_normals = {(vertex.bone, *vertex.normal) for vertex in triangle.vertices}
        if len(triangle_vertices) > max_vertices or len(triangle_normals) > max_normals or 1 > max_triangles:
            raise LargeTextureError("one SMD triangle exceeds the configured GoldSrc budget")
        exceeds = (
            current
            and (
                len(vertices | triangle_vertices) > max_vertices
                or len(normals | triangle_normals) > max_normals
                or len(current) + 1 > max_triangles
            )
        )
        if exceeds:
            flush()
            current = []
            vertices = set()
            normals = set()
        current.append(triangle)
        vertices.update(triangle_vertices)
        normals.update(triangle_normals)
    flush()
    return parts or [SmdDocument(document.path, list(document.bones), dict(document.frames), [])]


def write_smd(document: SmdDocument, path: Path | str) -> Path:
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)

    def number(value: float) -> str:
        if abs(value) < 0.0000005:
            value = 0.0
        return f"{value:.6f}"

    lines = ["version 1", "nodes"]
    lines.extend(f'{bone.index} "{bone.name.replace(chr(34), chr(92) + chr(34))}" {bone.parent}' for bone in document.bones)
    lines.extend(["end", "skeleton"])
    for frame, poses in sorted(document.frames.items()):
        lines.append(f"time {frame}")
        for pose in poses:
            values = [pose.bone, *pose.position, *pose.rotation]
            lines.append(" ".join(str(value) if isinstance(value, int) else number(value) for value in values))
    lines.extend(["end"])
    if document.triangles:
        lines.append("triangles")
        for triangle in document.triangles:
            lines.append(triangle.material)
            for vertex in triangle.vertices:
                values = [vertex.bone, *vertex.position, *vertex.normal, *vertex.uv]
                line = " ".join(str(value) if isinstance(value, int) else number(value) for value in values)
                if vertex.links:
                    line += " " + str(len(vertex.links))
                    for bone, weight in vertex.links:
                        line += f" {bone} {number(weight)}"
                lines.append(line)
        lines.append("end")
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return destination
