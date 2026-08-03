"""Dependency-free transform comparisons shared by host and Blender stages."""

from __future__ import annotations

from math import acos, cos, sin
from typing import Iterable


def euler_xyz_rotation_matrix(rotation: Iterable[float]) -> tuple[tuple[float, float, float], ...]:
    """Return the matrix for Blender/GoldSrc XYZ Euler channels."""

    x, y, z = (float(value) for value in rotation)
    cx, sx = cos(x), sin(x)
    cy, sy = cos(y), sin(y)
    cz, sz = cos(z), sin(z)
    return (
        (cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx),
        (sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx),
        (-sy, cy * sx, cy * cx),
    )


def rotation_matrix_angle(
    left: Iterable[Iterable[float]],
    right: Iterable[Iterable[float]],
) -> float:
    """Return the shortest angular difference between two rotation matrices."""

    left_rows = tuple(tuple(float(value) for value in row) for row in left)
    right_rows = tuple(tuple(float(value) for value in row) for row in right)
    trace = sum(
        left_rows[row][column] * right_rows[row][column]
        for row in range(3)
        for column in range(3)
    )
    cosine = max(-1.0, min(1.0, (trace - 1.0) * 0.5))
    return acos(cosine)


def euler_xyz_rotation_error(left: Iterable[float], right: Iterable[float]) -> float:
    return rotation_matrix_angle(
        euler_xyz_rotation_matrix(left),
        euler_xyz_rotation_matrix(right),
    )
