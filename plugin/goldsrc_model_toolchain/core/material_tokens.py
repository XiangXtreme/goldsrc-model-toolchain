"""Shared material-to-contract texture token resolution."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable


def resolve_texture_token(candidates: Iterable[Any], contract: dict[str, Any]) -> str | None:
    """Resolve Blender material/image hints exactly as the SMD exporter does."""

    texture_names = {
        str(texture["name"]).casefold(): str(texture["name"])
        for texture in contract.get("textures", [])
        if isinstance(texture, dict) and isinstance(texture.get("name"), str)
    }
    texture_sources = {
        Path(str(texture["source"])).name.casefold(): str(texture["name"])
        for texture in contract.get("textures", [])
        if (
            isinstance(texture, dict)
            and isinstance(texture.get("name"), str)
            and isinstance(texture.get("source"), str)
        )
    }
    large_aliases = {
        str(atlas["name"]).casefold(): str(atlas["name"])
        for atlas in contract.get("large_textures", [])
        if isinstance(atlas, dict) and isinstance(atlas.get("name"), str)
    }
    large_images = {
        Path(str(atlas["image"])).name.casefold(): str(atlas["name"])
        for atlas in contract.get("large_textures", [])
        if (
            isinstance(atlas, dict)
            and isinstance(atlas.get("name"), str)
            and isinstance(atlas.get("image"), str)
        )
    }
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        key = Path(candidate).name.casefold()
        if key in texture_names:
            return texture_names[key]
        if key in texture_sources:
            return texture_sources[key]
        if key in large_aliases:
            return large_aliases[key]
        if key in large_images:
            return large_images[key]
        if f"{key}.bmp" in large_aliases:
            return large_aliases[f"{key}.bmp"]
    return None
