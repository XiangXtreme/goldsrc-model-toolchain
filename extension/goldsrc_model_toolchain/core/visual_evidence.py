"""Small, Blender-independent helpers for readback visual evidence."""

from __future__ import annotations

from typing import Iterable, Mapping


AXIS_NAMES = ("X", "Y", "Z")


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
