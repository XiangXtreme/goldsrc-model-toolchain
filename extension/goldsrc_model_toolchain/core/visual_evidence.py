"""Blender-independent helpers for readable visual evidence."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Iterable, Mapping

from PIL import Image, ImageDraw, ImageFont, ImageOps


AXIS_NAMES = ("X", "Y", "Z")
_RESAMPLING = getattr(Image, "Resampling", Image)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _font(size: int):
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size=size)
    except OSError:
        return ImageFont.load_default(size=size)


def _fit_line(draw, text: object, font, maximum_width: int) -> str:
    value = str(text or "")
    if draw.textbbox((0, 0), value, font=font)[2] <= maximum_width:
        return value
    suffix = "..."
    while value and draw.textbbox((0, 0), value + suffix, font=font)[2] > maximum_width:
        value = value[:-1]
    return value + suffix if value else suffix


def representative_sample_labels(count: int) -> list[str]:
    """Return compact semantic labels for an ordered set of visual samples."""

    if count < 0:
        raise ValueError("sample count cannot be negative")
    labels = {
        0: [],
        1: ["SAMPLE"],
        2: ["START", "END"],
        3: ["START", "MID", "END"],
        4: ["START", "1/3", "2/3", "END"],
        5: ["START", "1/4", "MID", "3/4", "END"],
    }
    return labels.get(count, [f"SAMPLE {index + 1}" for index in range(count)])


def create_labeled_contact_sheet(
    items: Iterable[Mapping[str, object]],
    destination: str | Path,
    *,
    title: str | None = None,
    columns: int = 3,
    tile_width: int = 384,
    tile_height: int = 384,
    layout_path: str | Path | None = None,
) -> dict:
    """Compose labeled images without cropping or covering their image areas."""

    entries = [dict(item) for item in items]
    if not entries:
        raise ValueError("contact sheet requires at least one image")
    if columns < 1 or tile_width < 64 or tile_height < 64:
        raise ValueError("contact sheet columns must be positive and tiles must be at least 64 px")
    output = Path(destination).expanduser().resolve()
    if output.suffix.casefold() != ".png":
        raise ValueError("contact sheet destination must use the .png extension")
    sidecar = (
        Path(layout_path).expanduser().resolve()
        if layout_path is not None else output.with_suffix(".json")
    )
    if sidecar == output:
        raise ValueError("contact sheet image and layout report must use different paths")
    output.parent.mkdir(parents=True, exist_ok=True)
    sidecar.parent.mkdir(parents=True, exist_ok=True)

    margin = 16
    gap = 12
    caption_height = 62
    title_height = 48 if title else 0
    rows = math.ceil(len(entries) / columns)
    cell_width = tile_width
    cell_height = tile_height + caption_height
    sheet_width = margin * 2 + columns * cell_width + max(0, columns - 1) * gap
    sheet_height = margin * 2 + title_height + rows * cell_height + max(0, rows - 1) * gap
    sheet = Image.new("RGBA", (sheet_width, sheet_height), (35, 38, 43, 255))
    draw = ImageDraw.Draw(sheet)
    title_font = _font(22)
    label_font = _font(18)
    detail_font = _font(14)
    if title:
        display_title = _fit_line(draw, title, title_font, sheet_width - margin * 2)
        draw.text((margin, margin), display_title, fill=(245, 246, 248, 255), font=title_font)

    cells = []
    for index, entry in enumerate(entries):
        source = Path(str(entry.get("path", ""))).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"contact sheet image does not exist: {source}")
        row, column = divmod(index, columns)
        left = margin + column * (cell_width + gap)
        top = margin + title_height + row * (cell_height + gap)
        image_rect = [left, top, left + tile_width, top + tile_height]
        caption_rect = [left, top + tile_height, left + tile_width, top + cell_height]
        with Image.open(source) as opened:
            original_size = list(opened.size)
            source_image = opened.convert("RGBA")
            contained = ImageOps.contain(
                source_image, (tile_width - 2, tile_height - 2), method=_RESAMPLING.LANCZOS,
            )
        backdrop = Image.new("RGBA", (tile_width, tile_height), (24, 27, 31, 255))
        offset = ((tile_width - contained.width) // 2, (tile_height - contained.height) // 2)
        backdrop.alpha_composite(contained, dest=offset)
        sheet.alpha_composite(backdrop, dest=(left, top))
        draw.rectangle(image_rect, outline=(87, 93, 102, 255), width=1)
        draw.rectangle(caption_rect, fill=(45, 49, 55, 255), outline=(87, 93, 102, 255), width=1)

        maximum_text_width = tile_width - 20
        label = _fit_line(draw, entry.get("label", f"SAMPLE {index + 1}"), label_font, maximum_text_width)
        detail = _fit_line(draw, entry.get("detail", ""), detail_font, maximum_text_width)
        label_position = (left + 10, caption_rect[1] + 8)
        detail_position = (left + 10, caption_rect[1] + 35)
        draw.text(label_position, label, fill=(247, 248, 250, 255), font=label_font)
        draw.text(detail_position, detail, fill=(182, 188, 197, 255), font=detail_font)
        label_bbox = list(draw.textbbox(label_position, label, font=label_font))
        detail_bbox = list(draw.textbbox(detail_position, detail, font=detail_font))
        cells.append({
            "index": index,
            "row": row,
            "column": column,
            "source_path": str(source),
            "source_sha256": _sha256(source),
            "source_size": original_size,
            "image_rect": image_rect,
            "contained_rect": [
                left + offset[0], top + offset[1],
                left + offset[0] + contained.width, top + offset[1] + contained.height,
            ],
            "caption_rect": caption_rect,
            "label": str(entry.get("label", f"SAMPLE {index + 1}")),
            "detail": str(entry.get("detail", "")),
            "rendered_label": label,
            "rendered_detail": detail,
            "label_bbox": label_bbox,
            "detail_bbox": detail_bbox,
        })

    sheet.convert("RGB").save(output, format="PNG", optimize=True)
    report = {
        "path": str(output),
        "layout_path": str(sidecar),
        "sha256": _sha256(output),
        "bytes": output.stat().st_size,
        "size": [sheet_width, sheet_height],
        "rows": rows,
        "columns": columns,
        "tile_size": [tile_width, tile_height],
        "cell_size": [cell_width, cell_height],
        "title": title,
        "cells": cells,
    }
    sidecar.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def choose_front_axis(minimum: Iterable[float], maximum: Iterable[float]) -> dict:
    """Choose the thinnest model axis, preferring conventional Y-front on ties."""

    low = tuple(float(value) for value in minimum)
    high = tuple(float(value) for value in maximum)
    spans = tuple(max(0.0, high[index] - low[index]) for index in range(3))
    tie_priority = {1: 0, 0: 1, 2: 2}
    axis = min(range(3), key=lambda index: (spans[index], tie_priority[index]))
    projected = tuple(spans[index] for index in range(3) if index != axis)
    return {
        "axis": axis,
        "axis_name": AXIS_NAMES[axis],
        "spans": list(spans),
        "projected_spans": list(projected),
    }


def summarize_preview_visibility(previews: Iterable[Mapping[str, object]]) -> dict:
    fractions = [float(item.get("foreground_fraction", 0.0)) for item in previews]
    visible = [value for value in fractions if value > 0.0]
    return {
        "status": "not_applicable" if not fractions else "pass" if visible else "fail",
        "preview_count": len(fractions),
        "visible_preview_count": len(visible),
        "minimum_foreground_fraction": min(fractions, default=0.0),
        "maximum_foreground_fraction": max(fractions, default=0.0),
    }
