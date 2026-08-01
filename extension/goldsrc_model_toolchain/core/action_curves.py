"""Compatibility helpers for reading Blender Action F-curves."""

from __future__ import annotations

import math
from typing import Any, Iterator


def iter_action_fcurves(action: Any) -> Iterator[Any]:
    """Yield Action F-curves from legacy and Blender 5.2 storage layouts."""

    if action is None:
        return
    direct = getattr(action, "fcurves", None)
    if direct is not None:
        yield from direct
        return
    for layer in getattr(action, "layers", ()):
        for strip in getattr(layer, "strips", ()):
            for channelbag in getattr(strip, "channelbags", ()):
                yield from getattr(channelbag, "fcurves", ())


def representative_frame_samples(frame_range: Any, *, maximum: int = 5) -> list[int]:
    """Sample an Action without aliasing a symmetric loop to neutral poses."""

    if maximum < 1:
        raise ValueError("maximum frame samples must be positive")
    start = math.floor(float(frame_range[0]))
    end = math.ceil(float(frame_range[1]))
    if maximum == 1 or end <= start:
        return [start]
    return list(dict.fromkeys(
        round(start + (end - start) * index / (maximum - 1))
        for index in range(maximum)
    ))
