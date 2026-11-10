"""Turn a Figma file into a template: pages, geometry, styles and report lines."""

import re

from makeready.colour import Swatch, figma_rgb, hex_of, match
from makeready.geometry import IDENTITY, Affine, compose, from_figma, scaled
from makeready.text import tracking as letter_tracking

_SLOT = re.compile(r"^\{(.+)\}$")
_CONTAINERS = {"GROUP", "FRAME", "COMPONENT", "INSTANCE", "SECTION"}
_VECTORS = {"VECTOR", "BOOLEAN_OPERATION", "STAR", "REGULAR_POLYGON", "POLYGON", "LINE"}
_ALIGN = {"LEFT": "left", "CENTER": "center", "RIGHT": "right", "JUSTIFIED": "justify"}
_VALIGN = {"TOP": "top", "CENTER": "center", "BOTTOM": "bottom"}
_RESIZE = {"NONE", "WIDTH", "HEIGHT", "WIDTH_AND_HEIGHT"}
_BLEND_OK = {"PASS_THROUGH", "NORMAL"}


def extract(
    doc: dict,
    name: str,
    swatches: list[Swatch],
    frames: list[str] | None = None,
    scale: float = 1.0,
) -> tuple[dict, list[str]]:
    if not isinstance(name, str) or not name:
        raise ValueError("template name must be text")
    if isinstance(scale, bool) or not isinstance(scale, (int, float)):
        raise ValueError("scale must be a number")
    found = _pages(doc)
    if frames is not None:
        found = _select_frames(found, frames)
    report: list[str] = []
    pages = []
    used: set[str] = set()
    page_names: set[str] = set()
    for node in found:
        page_name = _page_file_name(node.get("name"), _label(node))
        # The proof finds each page's reference render by its exact name.
        if page_name in page_names:
            raise ValueError(f"{page_name}: two frames share this page name; rename one in Figma")
        page_names.add(page_name)
        clips = node.get("clipsContent", False)
        if not isinstance(clips, bool):
            raise ValueError(f"{page_name}: clipsContent must be true or false")
        if clips:
            report.append(f"warning: {page_name}: frame clips content; drawn bleed will be lost")
        elements: list[dict] = []
        # The page frame's transform is its place on the canvas, not on the page.
        _walk(_children(node, page_name), IDENTITY, scale, elements, report, used, swatches)
        width, height = _size(node, page_name)
        pages.append(
            {
                "name": page_name,
                "figma_id": _text_field(node, "id"),
                "width": width * scale,
                "height": height * scale,
                "elements": elements,
            }
        )
    return {"name": name, "scale": scale, "bleed": 9.0, "pages": pages}, report


def _pages(doc: dict) -> list[dict]:
    if not isinstance(doc, dict):
        raise ValueError("Figma file must be an object")
    document = doc.get("document")
    if not isinstance(document, dict):
        raise ValueError("Figma file: document must be an object")
    canvases = document.get("children")
    if not isinstance(canvases, list):
        raise ValueError("Figma file: document.children must be a list")
    found = []
    for canvas in canvases:
        if not isinstance(canvas, dict) or canvas.get("type") != "CANVAS":
            continue
        for node in _children(canvas, _label(canvas)):
            if node.get("type") == "FRAME":
                found.append(node)
    return found


def _select_frames(found: list[dict], frames: list[str]) -> list[dict]:
    if not isinstance(frames, list):
        raise ValueError("frames must be a list")
    known = {node.get("name") for node in found if isinstance(node.get("name"), str)}
    for wanted in frames:
        if wanted not in known:
            raise ValueError(f"unknown frame {wanted}")
    chosen = set(frames)
    return [node for node in found if node.get("name") in chosen]


def _walk(
    nodes: list,
    parent: Affine,
    scale: float,
    elements: list[dict],
    report: list[str],
    used: set[str],
    swatches: list[Swatch],
) -> None:
    for node in nodes:
        if not _visible(node):
            continue
        label = _label(node)
        kind_name = node.get("type")
        if not isinstance(kind_name, str) or not kind_name:
            raise ValueError(f"{label}: type must be text")
        matrix = compose(parent, _transform(node, label))
        if kind_name in ("FRAME", "INSTANCE") and _has_type(node.get("fills"), "SOLID", label):
            elements.append(_element(node, matrix, scale, "shape", report, used, swatches, shape="rect"))
        if kind_name in _CONTAINERS:
            _walk(_children(node, label), matrix, scale, elements, report, used, swatches)
            continue
        kind, shape = _classify(node, kind_name, label)
        if kind is None:
            continue
        elements.append(_element(node, matrix, scale, kind, report, used, swatches, shape=shape))


def _classify(node: dict, kind_name: str, label: str) -> tuple[str | None, str | None]:
    if kind_name == "TEXT":
        return "text", None
    if kind_name in ("RECTANGLE", "ELLIPSE") and _has_type(node.get("fills"), "IMAGE", label):
        return "image", None
    if kind_name == "RECTANGLE":
        return "shape", "rect"
    if kind_name == "ELLIPSE":
        return "shape", "oval"
    if kind_name in _VECTORS:
        return "vector", None
    return None, None


def _element(
    node: dict,
    matrix: Affine,
    scale: float,
    kind: str,
    report: list[str],
    used: set[str],
    swatches: list[Swatch],
    shape: str | None,
) -> dict:
    element_id, slot = _identity(node, used)
    fill, fill_notes, base_opacity = _matched(node.get("fills"), swatches, element_id)
    stroke, stroke_notes, _stroke_opacity = _matched(node.get("strokes"), swatches, element_id)
    if stroke is not None:
        stroke["weight"] = _number(node, "strokeWeight", element_id) * scale
    for note in fill_notes + stroke_notes:
        report.append(f"warning: {element_id}: {note}")
    _effects(node, element_id, report)
    _blend(node, element_id, report)
    width, height = _size(node, element_id)
    element = {
        "id": element_id,
        "figma_id": _text_field(node, "id"),
        "kind": kind,
        "slot": slot,
        "transform": list(scaled(matrix, scale)),
        "width": width * scale,
        "height": height * scale,
        "opacity": _layer_opacity(node, element_id),
        "fill": fill,
        "stroke": stroke,
    }
    if shape is not None:
        element["shape"] = shape
    if kind == "text":
        element["text"] = _text_block(node, scale, element_id, report, base_opacity)
    elif kind == "image":
        element["image"] = _image_block(node, element_id)
    return element


def _identity(node: dict, used: set[str]) -> tuple[str, str | None]:
    raw = node.get("name")
    if not isinstance(raw, str):
        raise ValueError(f"{_label(node)}: name must be text")
    base = raw.strip()
    found = _SLOT.fullmatch(base)
    slot: str | None = None
    if found:
        base = found.group(1)
        slot = base
    if not base:
        raise ValueError(f"{_label(node)}: name must be text")
    element_id = base
    if element_id in used:
        number = 2
        while f"{base}-{number}" in used:
            number += 1
        element_id = f"{base}-{number}"
    used.add(element_id)
    return element_id, slot


def _matched(
    paints: object,
    swatches: list[Swatch],
    label: str,
) -> tuple[dict | None, list[str], float | None]:
    visible = _visible_paints(paints, label)
    notes: list[str] = []
    chosen = None
    base_opacity = None
    for paint in visible:
        if paint["type"] != "SOLID":
            continue
        opacity = _paint_opacity(paint, label)
        rgb = figma_rgb(paint["color"])
        swatch = match(rgb, swatches)
        base_opacity = opacity
        if swatch is None:
            notes.append(f"colour {hex_of(rgb)} matches no brand swatch")
        else:
            chosen = {"swatch": swatch.name, "opacity": opacity}
        break
    for paint in visible:
        if paint["type"].startswith("GRADIENT_"):
            notes.append("gradient fill is not supported")
    return chosen, notes, base_opacity


def _text_block(
    node: dict,
    scale: float,
    element_id: str,
    report: list[str],
    base_opacity: float | None,
) -> dict:
    style = node.get("style")
    if not isinstance(style, dict):
        raise ValueError(f"{element_id}: style must be an object")
    characters = node.get("characters")
    if not isinstance(characters, str):
        raise ValueError(f"{element_id}: characters must be text")
    font_size = _style_number(style, "fontSize", element_id)
    if font_size == 0:
        raise ValueError(f"{element_id}: fontSize must not be 0")
    resize = style.get("textAutoResize", "NONE")
    if not isinstance(resize, str) or resize not in _RESIZE:
        raise ValueError(f"{element_id}: textAutoResize must be NONE, WIDTH, HEIGHT or WIDTH_AND_HEIGHT")
    _opacity_runs(node, element_id, report, base_opacity)
    trim = style.get("leadingTrim", "NONE")
    if not isinstance(trim, str):
        raise ValueError(f"{element_id}: leadingTrim must be text")
    if trim != "NONE":
        report.append(f"warning: {element_id}: trim leading is not supported")
    return {
        "characters": characters,
        "font": f"{_style_text(style, 'fontFamily', element_id)}/{_style_text(style, 'fontStyle', element_id)}",
        "size": font_size * scale,
        "tracking": letter_tracking(_style_number(style, "letterSpacing", element_id), font_size),
        "leading": _style_number(style, "lineHeightPx", element_id) * scale,
        "align": _mapped(style, "textAlignHorizontal", _ALIGN, element_id),
        "valign": _mapped(style, "textAlignVertical", _VALIGN, element_id),
        "auto_height": resize in ("HEIGHT", "WIDTH_AND_HEIGHT"),
    }


def _mapped(style: dict, key: str, mapping: dict[str, str], element_id: str) -> str:
    value = style.get(key)
    if not isinstance(value, str) or value not in mapping:
        raise ValueError(f"{element_id}: {key} must be {', '.join(mapping)}")
    return mapping[value]


def _opacity_runs(node: dict, element_id: str, report: list[str], base_opacity: float | None) -> None:
    overrides = node.get("characterStyleOverrides", [])
    table = node.get("styleOverrideTable", {})
    if not isinstance(overrides, list):
        raise ValueError(f"{element_id}: characterStyleOverrides must be a list")
    if not isinstance(table, dict):
        raise ValueError(f"{element_id}: styleOverrideTable must be an object")
    for index in overrides:
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError(f"{element_id}: characterStyleOverrides entries must be integers")
        if index == 0:
            continue
        entry = table.get(str(index))
        if entry is None:
            continue
        if not isinstance(entry, dict):
            raise ValueError(f"{element_id}: styleOverrideTable.{index} must be an object")
        if "fills" not in entry:
            continue
        opacity = _first_solid_opacity(entry.get("fills"), element_id)
        if opacity is None or base_opacity is None or opacity == base_opacity:
            continue
        report.append(f"warning: {element_id}: text runs with different opacities are not supported")
        return


def _first_solid_opacity(paints: object, label: str) -> float | None:
    for paint in _visible_paints(paints, label):
        if paint["type"] == "SOLID":
            return _paint_opacity(paint, label)
    return None


def _image_block(node: dict, label: str) -> dict:
    for paint in _visible_paints(node.get("fills"), label):
        if paint["type"] != "IMAGE":
            continue
        ref = paint.get("imageRef")
        mode = paint.get("scaleMode")
        if not isinstance(ref, str) or not ref:
            raise ValueError(f"{label}: imageRef must be text")
        if not isinstance(mode, str) or not mode:
            raise ValueError(f"{label}: scaleMode must be text")
        return {"ref": ref, "scale_mode": mode}
    raise ValueError(f"{label}: image fill is missing")


def _effects(node: dict, element_id: str, report: list[str]) -> None:
    effects = node.get("effects", [])
    if not isinstance(effects, list):
        raise ValueError(f"{_label(node)}: effects must be a list")
    for effect in effects:
        if not isinstance(effect, dict):
            raise ValueError(f"{_label(node)}: effect must be an object")
        visible = effect.get("visible", True)
        if not isinstance(visible, bool):
            raise ValueError(f"{_label(node)}: effect visible must be true or false")
        if not visible:
            continue
        kind = effect.get("type")
        if not isinstance(kind, str) or not kind:
            raise ValueError(f"{_label(node)}: effect type must be text")
        report.append(f"warning: {element_id}: effect {kind} left out")


def _blend(node: dict, element_id: str, report: list[str]) -> None:
    if "blendMode" not in node:
        return
    mode = node["blendMode"]
    if not isinstance(mode, str) or not mode:
        raise ValueError(f"{_label(node)}: blendMode must be text")
    if mode not in _BLEND_OK:
        report.append(f"warning: {element_id}: blend mode {mode} left out")


def _visible(node: dict) -> bool:
    if not isinstance(node, dict):
        raise ValueError("Figma node must be an object")
    if "visible" not in node:
        return True
    visible = node["visible"]
    if not isinstance(visible, bool):
        raise ValueError(f"{_label(node)}: visible must be true or false")
    return visible


def _children(node: dict, label: str) -> list:
    children = node.get("children", [])
    if not isinstance(children, list):
        raise ValueError(f"{label}: children must be a list")
    return children


def _has_type(paints: object, kind: str, label: str) -> bool:
    return any(paint["type"] == kind for paint in _visible_paints(paints, label))


def _visible_paints(paints: object, label: str) -> list[dict]:
    if paints is None:
        return []
    if not isinstance(paints, list):
        raise ValueError(f"{label}: paints must be a list")
    visible = []
    for paint in paints:
        if not isinstance(paint, dict):
            raise ValueError(f"{label}: paint must be an object")
        flag = paint.get("visible", True)
        if not isinstance(flag, bool):
            raise ValueError(f"{label}: paint visible must be true or false")
        if not flag:
            continue
        kind = paint.get("type")
        if not isinstance(kind, str) or not kind:
            raise ValueError(f"{label}: paint type must be text")
        visible.append(paint)
    return visible


def _paint_opacity(paint: dict, label: str) -> float:
    opacity = paint.get("opacity", 1)
    if isinstance(opacity, bool) or not isinstance(opacity, (int, float)):
        raise ValueError(f"{label}: paint opacity must be a number")
    colour = paint.get("color")
    if not isinstance(colour, dict):
        raise ValueError(f"{label}: colour must be an object")
    alpha = colour.get("a", 1)
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)):
        raise ValueError(f"{label}: colour a must be a number")
    for channel in ("r", "g", "b"):
        value = colour.get(channel)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{label}: colour {channel} must be a number")
    return opacity * alpha


def _transform(node: dict, label: str) -> Affine:
    raw = node.get("relativeTransform")
    if not isinstance(raw, list) or len(raw) != 2:
        raise ValueError(f"{label}: relativeTransform must be a 2 by 3 matrix")
    for row in raw:
        if not isinstance(row, list) or len(row) != 3:
            raise ValueError(f"{label}: relativeTransform must be a 2 by 3 matrix")
        for value in row:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{label}: relativeTransform must be numbers")
    return from_figma(raw)


def _size(node: dict, label: str) -> tuple[float, float]:
    raw = node.get("size")
    if not isinstance(raw, dict):
        raise ValueError(f"{label}: size must be an object")
    return _number(raw, "x", label), _number(raw, "y", label)


def _layer_opacity(node: dict, label: str) -> float:
    if "opacity" not in node:
        return 1
    return _number(node, "opacity", label)


def _number(data: dict, key: str, label: str) -> float:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}: {key} must be a number")
    return value


def _page_file_name(name: object, frame: str) -> str:
    if isinstance(name, str) and name not in {"", ".", ".."} and "/" not in name and "\\" not in name:
        return name
    shown = name if isinstance(name, str) and name else frame
    raise ValueError(f"{shown}: page name must be a single file name; rename the frame in Figma")


def _text_field(node: dict, key: str) -> str:
    value = node.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{_label(node)}: {key} must be text")
    return value


def _style_text(style: dict, key: str, element_id: str) -> str:
    value = style.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{element_id}: {key} must be text")
    return value


def _style_number(style: dict, key: str, element_id: str) -> float:
    value = style.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{element_id}: {key} must be a number")
    return value


def _label(node: dict) -> str:
    if isinstance(node, dict):
        node_id = node.get("id")
        if isinstance(node_id, str) and node_id:
            return node_id
        name = node.get("name")
        if isinstance(name, str) and name:
            return name
    return "Figma node"
