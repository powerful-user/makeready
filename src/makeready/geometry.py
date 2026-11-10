"""Affine transforms between Figma frames and InDesign spread coordinates."""

import math
from collections.abc import Sequence

Affine = tuple[float, float, float, float, float, float]

IDENTITY: Affine = (1, 0, 0, 1, 0, 0)


def translate(x: float, y: float) -> Affine:
    return (1, 0, 0, 1, x, y)


def from_figma(rt: list[list[float]]) -> Affine:
    """Figma relativeTransform is [[a, c, e], [b, d, f]]."""
    return (rt[0][0], rt[1][0], rt[0][1], rt[1][1], rt[0][2], rt[1][2])


def compose(outer: Affine, inner: Affine) -> Affine:
    """Apply inner first, then outer."""
    a1, b1, c1, d1, e1, f1 = outer
    a2, b2, c2, d2, e2, f2 = inner
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def apply(m: Affine, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def corners(m: Affine, w: float, h: float) -> list[tuple[float, float]]:
    return [apply(m, 0, 0), apply(m, w, 0), apply(m, w, h), apply(m, 0, h)]


def bbox(points: Sequence[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (min(xs), min(ys), max(xs), max(ys))


def rotation_deg(m: Affine) -> float:
    a, b, _c, _d, _e, _f = m
    return math.degrees(math.atan2(b, a))


def scaled(m: Affine, s: float) -> Affine:
    a, b, c, d, e, f = m
    return (a, b, c, d, s * e, s * f)


def to_spread(x: float, y: float, page_w: float, page_h: float) -> tuple[float, float]:
    return (x - page_w / 2, y - page_h / 2)
