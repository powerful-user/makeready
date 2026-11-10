"""Font metrics and the Figma-to-InDesign text formulas."""

from dataclasses import dataclass
from pathlib import Path

from fontTools.ttLib import TTFont


@dataclass(frozen=True)
class FontMetrics:
    ascender: int
    descender: int
    units_per_em: int


def read_metrics(path: Path) -> FontMetrics:
    with TTFont(path) as font:
        return FontMetrics(
            ascender=font["hhea"].ascent,
            descender=font["hhea"].descent,
            units_per_em=font["head"].unitsPerEm,
        )


def tracking(letter_spacing_px: float, font_size_px: float) -> float:
    return letter_spacing_px / font_size_px * 1000


def content_height(m: FontMetrics, size: float) -> float:
    return (m.ascender - m.descender) / m.units_per_em * size


def first_baseline(m: FontMetrics, size: float, leading: float) -> float:
    half_lead = (leading - content_height(m, size)) / 2
    return half_lead + m.ascender / m.units_per_em * size


def bottom_inset(m: FontMetrics, size: float, leading: float) -> float:
    half_lead = (leading - content_height(m, size)) / 2
    return half_lead - m.descender / m.units_per_em * size
