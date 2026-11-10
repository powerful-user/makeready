"""Brand font map and the fonts a resolved document uses."""

from dataclasses import dataclass
from pathlib import Path

import yaml

from makeready.copytext import parse
from makeready.spec import SpecError, _elements, slot_text

_STYLE_FONT = {"Bold": "Bold", "Italic": "Italic", "BoldItalic": "Bold Italic"}


@dataclass(frozen=True)
class FontEntry:
    family: str
    style: str
    file: str


def load_font_map(repo) -> dict[str, FontEntry]:
    path = Path(repo) / "brand" / "font-map.yaml"
    if not path.is_file():
        return {}
    data = _mapping(path)
    if "fonts" not in data or data["fonts"] is None:
        return {}
    fonts = data["fonts"]
    if not isinstance(fonts, dict):
        raise SpecError(f"{path}: fonts must be a mapping")
    loaded: dict[str, FontEntry] = {}
    for key, value in fonts.items():
        if not isinstance(key, str) or not key:
            raise SpecError(f"{path}: font names must be text")
        if not isinstance(value, dict):
            raise SpecError(f"{path}: {key} must be a mapping")
        family = _text_field(path, key, value, "family")
        style = _text_field(path, key, value, "style")
        file_name = _text_field(path, key, value, "file")
        expected = f"{family}/{style}"
        if key != expected:
            raise SpecError(f"{path}: {key} must be {expected}")
        if not _relative(file_name):
            raise SpecError(f"{path}: {key}.file must stay inside the fonts folder")
        loaded[key] = FontEntry(family, style, file_name)
    return loaded


def fonts_used(repo, resolved, template) -> list[str]:
    used: set[str] = set()
    for element in _elements(template):
        if element.get("kind") != "text" or _hidden(element, resolved.overrides):
            continue
        block = element.get("text")
        element_id = element["id"]
        if not isinstance(block, dict):
            raise SpecError(f"{element_id}: text must be a mapping")
        font = block.get("font")
        if not isinstance(font, str) or "/" not in font:
            raise SpecError(f"{element_id}: text.font must be Family/Style")
        used.add(font)
        sample = _sample(repo, resolved, element, block)
        if not sample:
            continue
        family = font.split("/", 1)[0]
        for paragraph in parse(sample):
            for run in paragraph.runs:
                style = _STYLE_FONT.get(run.style or "")
                if style:
                    used.add(f"{family}/{style}")
    return sorted(used)


def _sample(repo, resolved, element: dict, block: dict) -> str | None:
    slot = element.get("slot")
    if isinstance(slot, str) and slot and slot in resolved.slots:
        value = resolved.slots[slot]
        if not isinstance(value, str):
            return ""
        if value.endswith((".md", ".txt")):
            try:
                return slot_text(repo, resolved.series, value)
            except SpecError as error:
                if str(error).startswith("missing file:"):
                    return None
                raise
        return value
    characters = block.get("characters", "")
    if not isinstance(characters, str):
        raise SpecError(f"{element['id']}: text.characters must be text")
    return characters


def _hidden(element: dict, overrides: dict) -> bool:
    values = overrides.get(element["id"])
    return isinstance(values, dict) and values.get("hidden") is True


def _text_field(path: Path, key: str, value: dict, field: str) -> str:
    text = value.get(field)
    if not isinstance(text, str) or not text:
        raise SpecError(f"{path}: {key}.{field} must be text")
    return text


def _mapping(path: Path) -> dict:
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError) as error:
        raise SpecError(f"{path}: {error}") from error
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise SpecError(f"{path}: {error}") from error
    if not isinstance(data, dict):
        raise SpecError(f"{path}: document must be a mapping")
    return data


def _relative(value: str) -> bool:
    if "\\" in value or "\x00" in value:
        return False
    path = Path(value)
    if path.is_absolute():
        return False
    return all(part not in ("", ".", "..") for part in path.parts)
