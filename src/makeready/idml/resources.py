"""Swatches, paragraph styles and font families for an IDML package."""

import xml.etree.ElementTree as ET
from dataclasses import dataclass

from makeready.colour import Swatch

_IDPKG = "http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging"
ET.register_namespace("idPkg", _IDPKG)

_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
_ALIGN = {
    "left": "LeftAlign",
    "center": "CenterAlign",
    "right": "RightAlign",
    "justify": "LeftJustified",
}
_CHARACTER_STYLES = (
    ("Bold", "Bold"),
    ("Italic", "Italic"),
    ("BoldItalic", "Bold Italic"),
)


@dataclass
class ParaStyle:
    name: str
    family: str
    style: str
    size: float
    leading: float
    tracking: float
    align: str
    swatch: str | None = None


def fmt(x: float) -> str:
    """Round to four decimals and strip trailing zeros. `-0` becomes `0`."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise ValueError(f"not a number: {x!r}")
    text = f"{x:.4f}".rstrip("0").rstrip(".")
    if text in {"", "-0"}:
        return "0"
    return text


def graphic_xml(seed_xml: bytes, swatches: list[Swatch]) -> bytes:
    """Return Graphic.xml with brand swatches added and the seed colours removed."""
    root = _parse(seed_xml, "Resources/Graphic.xml")
    swatches = _swatches(swatches)
    templates = {
        "Process": _color_template(root, "Process", "AID Process", swatches),
        "Spot": _color_template(root, "Spot", "AID Spot", swatches),
    }
    names = {swatch.name for swatch in swatches}
    _drop(root, "Color", names | {"AID Process", "AID Spot"})
    taken = _selves(root)
    added = []
    for swatch in swatches:
        model = "Spot" if swatch.spot else "Process"
        self_id = f"Color/{swatch.name}"
        if self_id in taken:
            raise ValueError(f"Resources/Graphic.xml: {self_id} already exists")
        taken.add(self_id)
        attributes = dict(templates[model])
        attributes["Self"] = self_id
        attributes["Name"] = swatch.name
        attributes["Model"] = model
        attributes["Space"] = "CMYK"
        attributes["ColorValue"] = " ".join(fmt(value) for value in swatch.cmyk)
        added.append(ET.Element("Color", attributes))
    _insert_colors(root, added)
    return _serialize(root)


def styles_xml(seed_xml: bytes, styles: list[ParaStyle]) -> bytes:
    """Return Styles.xml with one paragraph style per entry and the bold/italic styles."""
    root = _parse(seed_xml, "Resources/Styles.xml")
    styles = _paragraph_styles(styles)
    paragraph = _required(root, "ParagraphStyle", "AID Body", "Resources/Styles.xml")
    character = _required(
        root, "CharacterStyle", "$ID/[No character style]", "Resources/Styles.xml"
    )
    group = root.find("RootParagraphStyleGroup")
    character_group = root.find("RootCharacterStyleGroup")
    if group is None or character_group is None:
        raise ValueError("Resources/Styles.xml: root style group is missing")
    _drop(root, "ParagraphStyle", {style.name for style in styles} | {"AID Body"})
    taken = _selves(root)
    for style in styles:
        group.append(_paragraph(paragraph, style, taken))
    _drop(root, "CharacterStyle", {name for name, _style in _CHARACTER_STYLES})
    taken = _selves(root)
    for name, font_style in _CHARACTER_STYLES:
        character_group.append(_character(character, name, font_style, taken))
    return _serialize(root)


def fonts_xml(seed_xml: bytes, families: dict[str, list[str]]) -> bytes:
    """Return Fonts.xml with one family per entry, replacing a family of the same name."""
    root = _parse(seed_xml, "Resources/Fonts.xml")
    families = _families(families)
    if not families:
        return _serialize(root)
    template = _required(root, "FontFamily", "Nimbus Sans", "Resources/Fonts.xml")
    template_font = template.find("Font")
    font_template = dict(template_font.attrib) if template_font is not None else {}
    template_attributes = dict(template.attrib)
    for name, styles in families.items():
        _write_family(root, name, styles, template_attributes, font_template)
    return _serialize(root)


def _swatches(swatches: list[Swatch]) -> list[Swatch]:
    if not isinstance(swatches, list):
        raise ValueError("Resources/Graphic.xml: swatches must be a list")
    by_name: dict[str, Swatch] = {}
    for swatch in swatches:
        if not isinstance(swatch, Swatch):
            raise ValueError("Resources/Graphic.xml: swatch must be a Swatch")
        if not isinstance(swatch.name, str) or not swatch.name:
            raise ValueError("Resources/Graphic.xml: swatch name must be a string")
        if not isinstance(swatch.spot, bool):
            raise ValueError(f"Resources/Graphic.xml: {swatch.name}: spot must be a boolean")
        values = swatch.cmyk
        if (
            not isinstance(values, tuple)
            or len(values) != 4
            or any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values)
        ):
            raise ValueError(f"Resources/Graphic.xml: {swatch.name}: cmyk must be four numbers")
        by_name[swatch.name] = swatch
    return list(by_name.values())


def _color_template(root: ET.Element, model: str, preferred: str, swatches: list[Swatch]) -> dict:
    if not any(("Spot" if swatch.spot else "Process") == model for swatch in swatches):
        return {}
    found = _find(root, "Color", preferred)
    if found is not None:
        return dict(found.attrib)
    normal = None
    fallback = None
    for color in root.iter("Color"):
        if color.get("Model") != model:
            continue
        if fallback is None:
            fallback = color
        if color.get("ColorOverride") == "Normal" and normal is None:
            normal = color
    chosen = normal if normal is not None else fallback
    if chosen is None:
        raise ValueError(f"Resources/Graphic.xml: no {preferred} colour to copy")
    return dict(chosen.attrib)


def _paragraph_styles(styles: list[ParaStyle]) -> list[ParaStyle]:
    if not isinstance(styles, list):
        raise ValueError("Resources/Styles.xml: styles must be a list")
    seen: set[str] = set()
    chosen: list[ParaStyle] = []
    for style in styles:
        if not isinstance(style, ParaStyle):
            raise ValueError("Resources/Styles.xml: style must be a ParaStyle")
        if not isinstance(style.name, str) or not style.name:
            raise ValueError("Resources/Styles.xml: style name must be a string")
        if style.align not in _ALIGN:
            raise ValueError(
                f"Resources/Styles.xml: {style.name}: unknown alignment {style.align!r}"
            )
        for label, value in (
            ("size", style.size),
            ("leading", style.leading),
            ("tracking", style.tracking),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"Resources/Styles.xml: {style.name}: {label} must be a number")
        if style.swatch is not None and not isinstance(style.swatch, str):
            raise ValueError(f"Resources/Styles.xml: {style.name}: swatch must be a string")
        if not isinstance(style.family, str) or not isinstance(style.style, str):
            raise ValueError(f"Resources/Styles.xml: {style.name}: font family and style must be strings")
        if style.name in seen:
            chosen = [item for item in chosen if item.name != style.name]
        seen.add(style.name)
        chosen.append(style)
    return chosen


def _families(families: dict[str, list[str]]) -> dict[str, list[str]]:
    if not isinstance(families, dict):
        raise ValueError("Resources/Fonts.xml: families must be a mapping")
    checked: dict[str, list[str]] = {}
    for name, styles in families.items():
        if not isinstance(name, str) or not name:
            raise ValueError("Resources/Fonts.xml: family name must be a string")
        if not isinstance(styles, list) or any(not isinstance(style, str) or not style for style in styles):
            raise ValueError(f"Resources/Fonts.xml: {name}: styles must be a list of strings")
        checked[name] = _unique(styles)
    return checked


def _paragraph(template: ET.Element, style: ParaStyle, taken: set[str]) -> ET.Element:
    self_id = f"ParagraphStyle/{style.name}"
    if self_id in taken:
        raise ValueError(f"Resources/Styles.xml: {self_id} already exists")
    taken.add(self_id)
    element = _clone(template)
    element.set("Self", self_id)
    element.set("Name", style.name)
    element.set("NextStyle", self_id)
    element.set("FontStyle", style.style)
    element.set("PointSize", fmt(style.size))
    element.set("Tracking", fmt(style.tracking))
    element.set("Justification", _ALIGN[style.align])
    element.set("FillColor", "Color/Black" if style.swatch is None else f"Color/{style.swatch}")
    element.set("StyleUniqueId", f"aid-{style.name}")
    properties = element.find("Properties")
    if properties is None:
        properties = ET.SubElement(element, "Properties")
    _set_child(properties, "AppliedFont", "string", style.family)
    _set_child(properties, "Leading", "unit", fmt(style.leading))
    return element


def _character(template: ET.Element, name: str, font_style: str, taken: set[str]) -> ET.Element:
    self_id = f"CharacterStyle/{name}"
    if self_id in taken:
        raise ValueError(f"Resources/Styles.xml: {self_id} already exists")
    taken.add(self_id)
    element = _clone(template)
    element.set("Self", self_id)
    element.set("Name", name)
    element.set("FontStyle", font_style)
    element.set("StyleUniqueId", f"aid-character-{name}")
    return element


def _write_family(
    root: ET.Element,
    name: str,
    styles: list[str],
    template_attributes: dict,
    font_template: dict,
) -> None:
    existing = _find(root, "FontFamily", name)
    index = None
    if existing is not None:
        index = list(root).index(existing)
        root.remove(existing)
    taken = _selves(root)
    attributes = dict(template_attributes)
    attributes["Name"] = name
    if existing is not None and existing.get("Self"):
        attributes["Self"] = existing.get("Self")
    else:
        attributes["Self"] = _fresh(taken, str(template_attributes.get("Self", "aidFontFamily")))
    taken.add(attributes["Self"])
    family = ET.Element("FontFamily", attributes)
    for style in styles:
        font = dict(font_template)
        font["FontStyleName"] = style
        font["Name"] = f"{name}\t{style}"
        font["FontFamily"] = name
        font["Self"] = _fresh(taken, f"{attributes['Self']}Fontn{name} {style}")
        family.append(ET.Element("Font", font))
    if index is None:
        composite = next((i for i, child in enumerate(root) if child.tag == "CompositeFont"), None)
        if composite is None:
            root.append(family)
        else:
            root.insert(composite, family)
    else:
        root.insert(index, family)


def _insert_colors(root: ET.Element, colors: list[ET.Element]) -> None:
    index = 0
    for position, child in enumerate(list(root)):
        if child.tag == "Color":
            index = position + 1
    for offset, color in enumerate(colors):
        root.insert(index + offset, color)


def _set_child(parent: ET.Element, tag: str, type_: str, text: str) -> None:
    child = parent.find(tag)
    if child is None:
        child = ET.SubElement(parent, tag)
    child.set("type", type_)
    child.text = text


def _required(root: ET.Element, tag: str, name: str, part: str) -> ET.Element:
    found = _find(root, tag, name)
    if found is None:
        raise ValueError(f"{part}: no {name} {tag} to copy")
    return found


def _find(root: ET.Element, tag: str, name: str) -> ET.Element | None:
    for element in root.iter(tag):
        if element.get("Name") == name:
            return element
    return None


def _drop(root: ET.Element, tag: str, names: set[str]) -> None:
    for element in list(root.iter(tag)):
        if element.get("Name") in names:
            _parent(root, element).remove(element)


def _parent(root: ET.Element, node: ET.Element) -> ET.Element:
    for parent in root.iter():
        for child in list(parent):
            if child is node:
                return parent
    raise ValueError("IDML element has no parent")


def _selves(root: ET.Element) -> set[str]:
    return {self_id for element in root.iter() if (self_id := element.get("Self"))}


def _fresh(taken: set[str], preferred: str) -> str:
    if preferred not in taken:
        taken.add(preferred)
        return preferred
    number = 2
    while f"{preferred}-{number}" in taken:
        number += 1
    chosen = f"{preferred}-{number}"
    taken.add(chosen)
    return chosen


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    chosen = []
    for item in items:
        if item not in seen:
            seen.add(item)
            chosen.append(item)
    return chosen


def _clone(element: ET.Element) -> ET.Element:
    return ET.fromstring(ET.tostring(element, encoding="unicode"))


def _parse(xml: bytes, part: str) -> ET.Element:
    try:
        return ET.fromstring(xml)
    except ET.ParseError as error:
        raise ValueError(f"{part}: {error}") from error


def _serialize(root: ET.Element) -> bytes:
    body = ET.tostring(root, encoding="unicode")
    return f"{_DECL}\n{body}\n".encode("utf-8")
