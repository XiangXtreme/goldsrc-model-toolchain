"""Blender-independent helpers for audited mesh material mappings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


STATIC_MATERIAL_AUDIT_FIELD = "static_material_audit"
STATIC_MATERIAL_AUDIT_PROPERTY = "goldsrc_static_material_audit"


def original_material(material: Any) -> Any:
    if material is None:
        return None
    return getattr(material, "original", None) or material


def material_identity(material: Any) -> dict[str, Any]:
    material = original_material(material)
    if material is None:
        return {"name": None, "library": None}
    library = getattr(material, "library", None)
    return {
        "name": str(getattr(material, "name_full", None) or material.name),
        "library": str(getattr(library, "filepath", None)) if library is not None else None,
    }


def material_key(material: Any) -> tuple[str | None, str | None, int]:
    material = original_material(material)
    identity = material_identity(material)
    pointer = int(material.as_pointer()) if material is not None else 0
    return identity["name"], identity["library"], pointer


def explicit_material_token(material: Any) -> str | None:
    material = original_material(material)
    if material is None or not hasattr(material, "get"):
        return None
    token = material.get("goldsrc_texture_token")
    return str(token) if isinstance(token, str) and token.strip() else None


@dataclass(frozen=True)
class MeshMaterialUsage:
    materials: tuple[Any, ...]
    polygon_indices: tuple[int, ...]
    distribution: tuple[dict[str, Any], ...]
    invalid_indices: tuple[int, ...]
    triangles: int


def inspect_mesh_material_usage(mesh: Any) -> MeshMaterialUsage:
    """Return material slots and face/triangle counts without trusting slot usage."""

    materials = tuple(getattr(mesh, "materials", ()))
    polygons = tuple(getattr(mesh, "polygons", ()))
    polygon_indices = tuple(int(getattr(polygon, "material_index", 0)) for polygon in polygons)
    invalid = tuple(sorted({
        index for index in polygon_indices
        if index < 0 or index >= len(materials)
    }))
    face_counts = [0] * len(materials)
    triangle_counts = [0] * len(materials)
    for polygon, index in zip(polygons, polygon_indices):
        if index < 0 or index >= len(materials):
            continue
        face_counts[index] += 1
        triangle_counts[index] += max(0, len(getattr(polygon, "vertices", ())) - 2)
    distribution = tuple({
        "slot": index,
        "material": material_identity(material),
        "token": explicit_material_token(material),
        "faces": face_counts[index],
        "triangles": triangle_counts[index],
        "used": face_counts[index] > 0,
    } for index, material in enumerate(materials))
    return MeshMaterialUsage(
        materials=materials,
        polygon_indices=polygon_indices,
        distribution=distribution,
        invalid_indices=invalid,
        triangles=sum(triangle_counts),
    )


def distribution_projection(
    distribution: Any,
    *,
    include_material: bool,
    include_token: bool,
) -> list[dict[str, Any]]:
    """Normalize persisted material distributions for exact comparisons."""

    result = []
    for item in distribution if isinstance(distribution, (list, tuple)) else ():
        if not isinstance(item, dict):
            continue
        projected = {
            "slot": int(item.get("slot", -1)),
            "faces": int(item.get("faces", 0)),
            "triangles": int(item.get("triangles", 0)),
        }
        if include_material:
            identity = item.get("material")
            projected["material"] = {
                "name": identity.get("name") if isinstance(identity, dict) else None,
                "library": identity.get("library") if isinstance(identity, dict) else None,
            }
        if include_token:
            projected["token"] = item.get("token")
        result.append(projected)
    return sorted(result, key=lambda item: item["slot"])


def aggregate_token_triangles(distribution: Any) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in distribution if isinstance(distribution, (list, tuple)) else ():
        if not isinstance(item, dict):
            continue
        token = item.get("token")
        if not isinstance(token, str) or not token:
            continue
        result[token] = result.get(token, 0) + int(item.get("triangles", 0))
    return dict(sorted(result.items(), key=lambda item: item[0].casefold()))
