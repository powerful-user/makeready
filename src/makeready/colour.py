"""Brand swatches and tolerant matching of Figma colours."""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


@dataclass(frozen=True)
class Swatch:
    name: str
    hex: str
    cmyk: tuple[float, float, float, float]
    spot: bool = False
    pantone: str | None = None


def load_swatches(path: Path) -> list[Swatch]:
    try:
        data = yaml.safe_load(Path(path).read_text())
    except (yaml.YAMLError, UnicodeDecodeError) as error:
        raise ValueError(f"{path}: {error}") from error
    entries = data.get("swatches") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise ValueError(f"{path}: swatches must be a list")
    swatches: list[Swatch] = []
    seen: set[str] = set()
    for item in entries:
        if not isinstance(item, dict) or "name" not in item:
            raise ValueError(f"{path}: swatch needs a name")
        name = item["name"]
        if not isinstance(name, str) or not name:
            raise ValueError(f"{path}: swatch name must be text")
        if name in seen:
            raise ValueError(f"{path}: duplicate swatch name {name}")
        seen.add(name)
        raw_hex = item.get("hex")
        if not isinstance(raw_hex, str) or _HEX.fullmatch(raw_hex) is None:
            raise ValueError(f"{path}: {name}: hex must be #RRGGBB")
        spot = item.get("spot", False)
        if not isinstance(spot, bool):
            raise ValueError(f"{path}: {name}: spot must be true or false")
        pantone = item.get("pantone")
        if pantone is not None and not isinstance(pantone, str):
            raise ValueError(f"{path}: {name}: pantone must be a string")
        swatches.append(
            Swatch(
                name=name,
                hex=raw_hex.upper(),
                cmyk=_cmyk(path, name, item.get("cmyk")),
                spot=spot,
                pantone=pantone,
            )
        )
    return swatches


def _cmyk(path: Path, name: str, value: object) -> tuple[float, float, float, float]:
    message = f"{path}: {name}: cmyk must be four numbers from 0 to 100"
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(message)
    numbers: list[float] = []
    for channel in value:
        if isinstance(channel, bool) or not isinstance(channel, (int, float)):
            raise ValueError(message)
        number = float(channel)
        if number < 0 or number > 100:
            raise ValueError(message)
        numbers.append(number)
    return (numbers[0], numbers[1], numbers[2], numbers[3])


def figma_rgb(colour: dict) -> tuple[int, int, int]:
    return (round(colour["r"] * 255), round(colour["g"] * 255), round(colour["b"] * 255))


def hex_of(rgb: tuple[int, int, int]) -> str:
    red, green, blue = rgb
    return f"#{red:02X}{green:02X}{blue:02X}"


def match(
    rgb: tuple[int, int, int],
    swatches: list[Swatch],
    tolerance: int = 2,
) -> Swatch | None:
    nearest: Swatch | None = None
    nearest_distance: int | None = None
    for swatch in swatches:
        red = int(swatch.hex[1:3], 16)
        green = int(swatch.hex[3:5], 16)
        blue = int(swatch.hex[5:7], 16)
        distance = max(abs(rgb[0] - red), abs(rgb[1] - green), abs(rgb[2] - blue))
        if distance <= tolerance and (nearest_distance is None or distance < nearest_distance):
            nearest = swatch
            nearest_distance = distance
    return nearest
