"""Compile a resolved document and its template into IDML parts."""

import importlib.util

import re
import subprocess
import zipfile
import zlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from fontTools.ttLib import TTLibError
from PIL import Image, UnidentifiedImageError

from makeready.checks import place
from makeready.colour import load_swatches
from makeready.copytext import Para, Run, parse
from makeready.crop import crop
from makeready.fontmap import fonts_used, load_font_map
from makeready.geometry import bbox, compose, corners, translate
from makeready.idml.package import aid_instruction
from makeready.idml.resources import ParaStyle, fonts_xml, graphic_xml, styles_xml
from makeready.spec import SpecError, hooks_file, slot_text
from makeready.text import bottom_inset, first_baseline, read_metrics

_NS = "http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging"
ET.register_namespace("idPkg", _NS)
_SPREAD = f"{{{_NS}}}Spread"
_STORY = f"{{{_NS}}}Story"
_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
_KAPPA = 0.5522847498307936
_VALIGN = {"top": "TopAlign", "center": "CenterAlign", "bottom": "BottomAlign"}
_ALIGNS = {"left", "center", "right", "justify"}
_FITS = {"fill", "fit", "none"}
_KINDS = {"text", "image", "shape", "vector"}
_VECTORS = {".pdf", ".ai"}
_KEEP = {"Properties", "FlattenerPreference", "TimingSetting"}
_NO_STYLE = "CharacterStyle/$ID/[No character style]"
_PAGE_SIZE = re.compile(r"Page size:\s+([0-9.]+)\s+x\s+([0-9.]+)\s+pts")
_IMAGE_TYPES = {
    ".jpg": "$ID/JPEG",
    ".jpeg": "$ID/JPEG",
    ".png": "$ID/PNG",
    ".gif": "$ID/GIF",
    ".tif": "$ID/TIFF",
    ".tiff": "$ID/TIFF",
}


@dataclass
class Compiled:
    parts: dict[str, bytes]
    geometry: dict
    links: dict[str, Path]
    fonts: list[Path]


@dataclass
class _Seed:
    path: Path
    designmap: ET.Element
    preferences: ET.Element
    graphic: bytes
    styles: bytes
    fonts: bytes
    spread: ET.Element
    page: ET.Element
    story: ET.Element
    text_frame: ET.Element
    shape: ET.Element
    oval: ET.Element | None
    image_frame: ET.Element
    pdf_frame: ET.Element
    dropped_stories: set[str]
    aid: bytes


@dataclass
class _Build:
    repo: Path
    cfg: object
    resolved: object
    seed: _Seed
    ids: "_Ids"
    font_map: dict
    links: dict[str, Path] = field(default_factory=dict)
    stories: list = field(default_factory=list)
    styles: list = field(default_factory=list)
    hook: object = None


@dataclass(frozen=True)
class HookContext:
    """What a template hook is told about the frame it sees: `new_id()` gives a fresh
    item id, and `series`, `doc` and `page` say where the frame is being built."""

    new_id: object
    series: str
    doc: str
    page: str


class _Ids:
    def __init__(self) -> None:
        self._n = 0

    def next(self) -> str:
        self._n += 1
        return f"aid{self._n}"


def compile_document(repo, cfg, resolved, template) -> Compiled:
    """Compile one document to IDML parts, geometry, links and font files."""
    repo = Path(repo)
    build = _Build(
        repo=repo,
        cfg=cfg,
        resolved=resolved,
        seed=_load_seed(repo / "seed" / "reference.idml"),
        ids=_Ids(),
        font_map=load_font_map(repo),
    )
    build.hook = _load_hook(repo, resolved.template, template)
    pages = _pages(template)
    bleed = _bleed(template)
    parts: dict[str, bytes] = {}
    spread_refs = []
    geometry_pages = []
    for index, page, name, width, height in pages:
        spread_id, spread_bytes, page_geometry = _compile_page(
            build, page, index, name, width, height, bleed
        )
        spread_name = f"Spreads/Spread_{spread_id}.xml"
        parts[spread_name] = spread_bytes
        spread_refs.append(ET.Element(_SPREAD, {"src": spread_name}))
        geometry_pages.append(page_geometry)
    story_refs = []
    story_ids = []
    for story_id, story_bytes in build.stories:
        story_name = f"Stories/Story_{story_id}.xml"
        parts[story_name] = story_bytes
        story_refs.append(ET.Element(_STORY, {"src": story_name}))
        story_ids.append(story_id)
    parts["designmap.xml"] = _designmap(build.seed, spread_refs, story_refs, story_ids)
    parts["Resources/Preferences.xml"] = _preferences(
        build.seed.preferences, pages[0][3], pages[0][4], bleed
    )
    swatches = _brand_swatches(repo)
    parts["Resources/Graphic.xml"] = _call_resource(graphic_xml, build.seed.graphic, swatches)
    parts["Resources/Styles.xml"] = _call_resource(styles_xml, build.seed.styles, build.styles)
    families, font_paths = _fonts(build, template)
    parts["Resources/Fonts.xml"] = _call_resource(fonts_xml, build.seed.fonts, families)
    return Compiled(parts, {"pages": geometry_pages}, build.links, font_paths)


def _compile_page(build, page, index, name, width, height, bleed):
    spread = _clone(build.seed.spread)
    spread_id = build.ids.next()
    spread.set("Self", spread_id)
    spread.set("PageCount", "1")
    for child in list(spread):
        if child.tag not in _KEEP:
            spread.remove(child)
    page_item = _clone(build.seed.page)
    _reidentify(page_item, build.ids)
    page_item.set("GeometricBounds", f"0 0 {_num(height)} {_num(width)}")
    page_item.set("ItemTransform", f"1 0 0 1 {_num(-width / 2)} {_num(-height / 2)}")
    page_item.set("AppliedMaster", "n")
    spread.append(page_item)
    geometry = []
    for element in page["elements"]:
        if not isinstance(element, dict):
            raise SpecError(f"template: pages[{index}] elements must be mappings")
        element_id = _element_id(element, index)
        if _hidden(element, build.resolved.overrides):
            continue
        kind = element.get("kind")
        if not isinstance(kind, str) or kind not in _KINDS:
            raise SpecError(f"{element_id}: kind must be text, image, shape or vector")
        placed = place(element, build.resolved.overrides)
        item = _item(build, element, element_id, kind, placed, width, height)
        for out in _hooked(build, element, element_id, item, name):
            spread.append(out)
        box = bbox(corners(tuple(placed["transform"]), placed["width"], placed["height"]))
        geometry.append({"id": element_id, "bbox": [box[0], box[1], box[2], box[3]]})
    wrapper = ET.Element(_SPREAD, {"DOMVersion": "21.5"})
    wrapper.append(spread)
    page_geometry = {
        "name": name,
        "width": width,
        "height": height,
        "bleed": bleed,
        "elements": geometry,
    }
    return spread_id, _xml(wrapper), page_geometry


def _item(build, element, element_id, kind, placed, page_w, page_h):
    if kind == "text":
        return _text_frame(build, element, element_id, placed, page_w, page_h)
    if kind == "shape":
        return _shape(build, element, element_id, placed, page_w, page_h)
    if kind == "image":
        return _image(build, element, element_id, placed, page_w, page_h)
    return _vector(build, element, element_id, placed, page_w, page_h)


def _text_frame(build, element, element_id, placed, page_w, page_h):
    block = element.get("text")
    if not isinstance(block, dict):
        raise SpecError(f"{element_id}: text must be a mapping")
    values = _override_values(element, build.resolved)
    size, tracking, leading = _text_numbers(block, values, element_id)
    align = block.get("align")
    if not isinstance(align, str) or align not in _ALIGNS:
        raise SpecError(f"{element_id}: text.align must be left, center, right or justify")
    valign = block.get("valign")
    if not isinstance(valign, str) or valign not in _VALIGN:
        raise SpecError(f"{element_id}: text.valign must be top, center or bottom")
    auto = block.get("auto_height")
    if not isinstance(auto, bool):
        raise SpecError(f"{element_id}: text.auto_height must be true or false")
    family, style_name = _font_name(block.get("font"), element_id)
    font_path = _mapped_font(build, f"{family}/{style_name}", element_id)
    metrics = _read_font(font_path, element_id)
    build.styles.append(
        ParaStyle(
            element_id,
            family,
            style_name,
            size,
            leading,
            tracking,
            align,
            _fill_swatch(element, values, element_id),
        )
    )
    story_id = build.ids.next()
    text = _copy(build, element, block, element_id)
    build.stories.append((story_id, _story(build.seed, story_id, element_id, text, values)))
    frame = _clone(build.seed.text_frame)
    _reidentify(frame, build.ids)
    frame.set("ParentStory", story_id)
    frame.set("ItemTransform", _spread_matrix(page_w, page_h, placed["transform"]))
    _set_geometry(frame, placed["width"], placed["height"], element_id, oval=False)
    _text_preference(frame, metrics, size, leading, valign, auto, placed["width"])
    _set_opacity(frame, "TransparencySetting", _object_opacity(element, values, element_id))
    _set_opacity(frame, "ContentTransparencySetting", _fill_opacity(element, element_id))
    _set_opacity(frame, "FillTransparencySetting", 1)
    return frame


def _shape(build, element, element_id, placed, page_w, page_h):
    shape = element.get("shape", "rect")
    if shape == "oval":
        if build.seed.oval is None:
            raise SpecError(f"{build.seed.path}: no oval to copy")
        frame = _clone(build.seed.oval)
        oval = True
    elif shape == "rect":
        frame = _clone(build.seed.shape)
        oval = False
    else:
        raise SpecError(f"{element_id}: shape must be rect or oval")
    _place_frame(frame, build.ids, placed, page_w, page_h, element_id, oval)
    values = _override_values(element, build.resolved)
    swatch, weight, stroke_opacity = _stroke(element, element_id)
    frame.set("FillColor", _swatch_ref(_fill_swatch(element, values, element_id)))
    frame.set("StrokeColor", _swatch_ref(swatch))
    frame.set("StrokeWeight", _num(weight))
    _set_opacity(frame, "TransparencySetting", _object_opacity(element, values, element_id))
    _set_opacity(frame, "FillTransparencySetting", _fill_opacity(element, element_id))
    _set_opacity(frame, "StrokeTransparencySetting", stroke_opacity)
    _set_paths(frame, element, element_id)
    nonprinting = element.get("nonprinting", False)
    if not isinstance(nonprinting, bool):
        raise SpecError(f"{element_id}: nonprinting must be true or false")
    if nonprinting:
        frame.set("Nonprinting", "true")
    return frame


def _image(build, element, element_id, placed, page_w, page_h):
    frame = _clone(build.seed.image_frame)
    _place_frame(frame, build.ids, placed, page_w, page_h, element_id, False)
    image = frame.find("Image")
    if image is None:
        raise SpecError(f"{build.seed.path}: no placed image to copy")
    path = _image_file(build, element, element_id)
    px_w, px_h, dpi = _raster_info(path)
    img_w = px_w * 72 / dpi
    img_h = px_h * 72 / dpi
    fit, focus = _fit_focus(build.resolved, element, element_id)
    try:
        cropped = crop(placed["width"], placed["height"], img_w, img_h, fit, focus)
    except ValueError as error:
        raise SpecError(f"{element_id}: {error}") from error
    scale = 72 / dpi * cropped.scale
    image.set("ItemTransform", _matrix((scale, 0, 0, scale, cropped.x, cropped.y)))
    _bounds(image, px_w, px_h, element_id)
    kind = _IMAGE_TYPES.get(path.suffix.lower())
    if kind is not None:
        image.set("ImageTypeName", kind)
    name = _link_name(build.links, element_id, path)
    _set_link(image, _link_uri(build, name), element_id)
    if element.get("place") is not None:
        image.set("ItemTransform", _matrix(_place_matrix(element["place"], element_id)))
    _set_paths(frame, element, element_id)
    _set_opacity(frame, "TransparencySetting", _object_opacity(element, _override_values(element, build.resolved), element_id))
    return frame


def _vector(build, element, element_id, placed, page_w, page_h):
    frame = _clone(build.seed.pdf_frame)
    _place_frame(frame, build.ids, placed, page_w, page_h, element_id, False)
    pdf = frame.find("PDF")
    if pdf is None:
        raise SpecError(f"{build.seed.path}: no placed PDF to copy")
    path = _asset_file(build.repo, element, element_id)
    if path.suffix.lower() not in _VECTORS:
        raise SpecError(f"{element_id}: unsupported asset {element.get('asset')}")
    pdf_w, pdf_h = _pdf_size(path)
    scale = min(placed["width"] / pdf_w, placed["height"] / pdf_h)
    x = (placed["width"] - pdf_w * scale) / 2
    y = (placed["height"] - pdf_h * scale) / 2
    pdf.set("ItemTransform", _matrix((scale, 0, 0, scale, x, y)))
    _bounds(pdf, pdf_w, pdf_h, element_id)
    name = _link_name(build.links, element_id, path)
    _set_link(pdf, _link_uri(build, name), element_id)
    values = _override_values(element, build.resolved)
    _set_opacity(frame, "TransparencySetting", _object_opacity(element, values, element_id))
    return frame


def _story(seed, story_id, style_name, text, values):
    story = _clone(seed.story)
    story.set("Self", story_id)
    for child in list(story):
        if child.tag == "ParagraphStyleRange":
            story.remove(child)
    paragraphs = parse(text) or [Para([Run("", None)], False)]
    attrs = {}
    if "size" in values:
        attrs["PointSize"] = _num(values["size"])
    if "tracking" in values:
        attrs["Tracking"] = _num(values["tracking"])
    leading = _num(values["leading"]) if "leading" in values else None
    for index, para in enumerate(paragraphs):
        story.append(_paragraph(style_name, para, attrs, leading, index == len(paragraphs) - 1))
    wrapper = ET.Element(_STORY, {"DOMVersion": "21.5"})
    wrapper.append(story)
    return _xml(wrapper)


def _paragraph(style_name, para, attrs, leading, last):
    element_attrs = {"AppliedParagraphStyle": f"ParagraphStyle/{style_name}"}
    if para.bullet:
        element_attrs["BulletsAndNumberingListType"] = "BulletList"
    element = ET.Element("ParagraphStyleRange", element_attrs)
    runs = para.runs or [Run("", None)]
    for run in runs:
        element.append(_run(run, attrs, leading))
    if not last:
        element.append(ET.Element("Br"))
    return element


def _run(run, attrs, leading):
    applied = _NO_STYLE if not run.style else f"CharacterStyle/{run.style}"
    element = ET.Element("CharacterStyleRange", {"AppliedCharacterStyle": applied, **attrs})
    if leading is not None:
        props = ET.SubElement(element, "Properties")
        node = ET.SubElement(props, "Leading", {"type": "unit"})
        node.text = leading
    content = ET.SubElement(element, "Content")
    content.text = run.text if run.text else ""
    return element


def _place_frame(frame, ids, placed, page_w, page_h, element_id, oval):
    _reidentify(frame, ids)
    frame.set("ItemTransform", _spread_matrix(page_w, page_h, placed["transform"]))
    _set_geometry(frame, placed["width"], placed["height"], element_id, oval)


def _text_preference(frame, metrics, size, leading, valign, auto, width):
    pref = frame.find("TextFramePreference")
    if pref is None:
        raise SpecError("seed text frame has no text preferences")
    pref.set("FirstBaselineOffset", "FixedHeight")
    pref.set("MinimumFirstBaselineOffset", _num(first_baseline(metrics, size, leading)))
    pref.set("VerticalJustification", _VALIGN[valign])
    pref.set("TextColumnFixedWidth", _num(width))
    if auto:
        pref.set("AutoSizingType", "HeightOnly")
        pref.set("AutoSizingReferencePoint", "TopCenterPoint")
    else:
        pref.set("AutoSizingType", "Off")
        if "AutoSizingReferencePoint" in pref.attrib:
            del pref.attrib["AutoSizingReferencePoint"]
    bottom = bottom_inset(metrics, size, leading) if valign in {"center", "bottom"} else 0
    _set_insets(pref, (0, 0, bottom, 0))


def _set_insets(pref, values):
    spacing = pref.find("Properties/InsetSpacing")
    if spacing is None:
        raise SpecError("seed text frame has no inset spacing")
    items = list(spacing)
    if len(items) != 4:
        raise SpecError("seed text frame insets must be four values")
    for item, value in zip(items, values):
        item.set("type", "unit")
        item.text = _num(value)


def _set_geometry(item, width, height, element_id, oval):
    props = item.find("Properties")
    if props is None:
        props = ET.Element("Properties")
        item.insert(0, props)
    for child in list(props):
        if child.tag in {"PathGeometry", "Label"}:
            props.remove(child)
    props.insert(0, _label(element_id))
    props.insert(0, _path(width, height, oval))


def _set_paths(frame, element, element_id):
    """Replace a frame's outline with the element's closed `paths`, in its own points.
    One path makes a polygon frame; several make one compound frame."""
    paths = element.get("paths")
    if paths is None:
        return
    problem = f"{element_id}: paths must be a list of closed paths, each three or more [x, y] points"
    if not isinstance(paths, list) or not paths:
        raise SpecError(problem)
    geometry = ET.Element("PathGeometry")
    for path in paths:
        if not isinstance(path, list) or len(path) < 3:
            raise SpecError(problem)
        kind = ET.SubElement(geometry, "GeometryPathType", {"PathOpen": "false"})
        points = ET.SubElement(kind, "PathPointArray")
        for point in path:
            if not isinstance(point, (list, tuple)) or len(point) != 2 or not all(_number(v) for v in point):
                raise SpecError(problem)
            pair = _pair(point)
            ET.SubElement(points, "PathPointType", {"Anchor": pair, "LeftDirection": pair, "RightDirection": pair})
    props = frame.find("Properties")
    old = props.find("PathGeometry")
    props.insert(list(props).index(old), geometry)
    props.remove(old)
    frame.tag = "Polygon"


def _place_matrix(value, element_id):
    """`place`: six numbers mapping image pixels to frame points."""
    if not isinstance(value, (list, tuple)) or len(value) != 6 or not all(_number(v) for v in value):
        raise SpecError(f"{element_id}: place must be six numbers")
    return tuple(float(v) for v in value)


def _load_hook(repo, template_name, template):
    path = hooks_file(repo, template_name, template)
    if path is None:
        return None
    spec = importlib.util.spec_from_file_location(f"makeready_hooks_{template_name}", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as error:
        raise SpecError(f"templates/{template_name}/{path.name}: {error}") from error
    hook = getattr(module, "item", None)
    if not callable(hook):
        raise SpecError(f"templates/{template_name}/{path.name}: define item(element, frame, context)")
    return hook


def _hooked(build, element, element_id, item, page_name):
    """Pass an item through the template hook: None keeps it, an element replaces it,
    a list puts every element in its place."""
    if build.hook is None:
        return [item]
    try:
        context = HookContext(
            build.ids.next, build.resolved.series, build.resolved.doc, page_name
        )
        result = build.hook(element, item, context)
    except SpecError:
        raise
    except Exception as error:
        raise SpecError(f"{element_id}: hook failed: {error}") from error
    if result is None:
        return [item]
    if isinstance(result, ET.Element):
        return [result]
    if isinstance(result, list) and all(isinstance(r, ET.Element) for r in result):
        return result
    raise SpecError(f"{element_id}: hook must return None, an element or a list of elements")


def _path(width, height, oval):
    geometry = ET.Element("PathGeometry")
    kind = ET.SubElement(geometry, "GeometryPathType", {"PathOpen": "false"})
    points = ET.SubElement(kind, "PathPointArray")
    for anchor, left, right in (_oval_points(width, height) if oval else _rect_points(width, height)):
        points.append(
            ET.Element(
                "PathPointType",
                {"Anchor": _pair(anchor), "LeftDirection": _pair(left), "RightDirection": _pair(right)},
            )
        )
    return geometry


def _rect_points(width, height):
    corners_ = ((0, 0), (0, height), (width, height), (width, 0))
    return tuple((point, point, point) for point in corners_)


def _oval_points(width, height):
    cx, cy = width / 2, height / 2
    rx, ry = width / 2, height / 2
    kx, ky = _KAPPA * rx, _KAPPA * ry
    return (
        ((cx, cy + ry), (cx - kx, cy + ry), (cx + kx, cy + ry)),
        ((cx + rx, cy), (cx + rx, cy + ky), (cx + rx, cy - ky)),
        ((cx, cy - ry), (cx + kx, cy - ry), (cx - kx, cy - ry)),
        ((cx - rx, cy), (cx - rx, cy - ky), (cx - rx, cy + ky)),
    )


def _label(element_id):
    label = ET.Element("Label")
    label.append(ET.Element("KeyValuePair", {"Key": "aid.id", "Value": str(element_id)}))
    return label


def _bounds(graphic, right, bottom, element_id):
    bounds = graphic.find("Properties/GraphicBounds")
    if bounds is None:
        raise SpecError(f"{element_id}: seed graphic has no bounds")
    bounds.set("Left", "0")
    bounds.set("Top", "0")
    bounds.set("Right", _num(right))
    bounds.set("Bottom", _num(bottom))


def _set_link(graphic, uri, element_id):
    link = graphic.find("Link")
    if link is None:
        raise SpecError(f"{element_id}: seed graphic has no link")
    link.set("LinkResourceURI", uri)


def _set_opacity(parent, tag, opacity):
    percent = _percent(opacity)
    found = parent.find(tag)
    if percent is None:
        if found is not None:
            parent.remove(found)
        return
    if found is None:
        found = ET.Element(tag)
        index = 0
        for position, child in enumerate(list(parent)):
            if child.tag in {
                "TransparencySetting",
                "FillTransparencySetting",
                "StrokeTransparencySetting",
                "ContentTransparencySetting",
                "TextWrapPreference",
                "ObjectExportOption",
            }:
                index = position + 1
        parent.insert(index, found)
    blend = found.find("BlendingSetting")
    if blend is None:
        blend = ET.SubElement(found, "BlendingSetting")
    blend.set("Opacity", percent)


def _designmap(seed, spread_refs, story_refs, story_ids):
    root = seed.designmap
    children = list(root)
    for child in children:
        root.remove(child)
    rebuilt = []
    placed_spread = False
    placed_story = False
    for child in children:
        if child.tag == _SPREAD:
            if not placed_spread:
                rebuilt.extend(spread_refs)
                placed_spread = True
            continue
        if child.tag == _STORY:
            if not placed_story:
                rebuilt.extend(story_refs)
                placed_story = True
            continue
        rebuilt.append(child)
    if not placed_spread:
        raise SpecError("designmap.xml: no spread reference to replace")
    if story_refs and not placed_story:
        raise SpecError("designmap.xml: no story reference to replace")
    for child in rebuilt:
        root.append(child)
    kept = [token for token in (root.get("StoryList") or "").split() if token not in seed.dropped_stories]
    root.set("StoryList", " ".join([*story_ids, *kept]))
    written = _xml(root)
    declaration, body = written.split(b"\n", 1)
    return declaration + b"\n" + seed.aid + b"\n" + body


def _preferences(root, width, height, bleed):
    found = list(root.iter("DocumentPreference"))
    if not found:
        raise SpecError("Resources/Preferences.xml: no DocumentPreference")
    for pref in found:
        pref.set("PageWidth", _num(width))
        pref.set("PageHeight", _num(height))
        pref.set("FacingPages", "false")
        pref.set("DocumentBleedUniformSize", "true")
        for side in ("Top", "Bottom", "InsideOrLeft", "OutsideOrRight"):
            pref.set(f"DocumentBleed{side}Offset", _num(bleed))
    return _xml(root)


def _call_resource(function, seed_xml, argument):
    try:
        return function(seed_xml, argument)
    except ValueError as error:
        raise SpecError(str(error)) from error


def _fonts(build, template):
    families: dict[str, list[str]] = {}
    paths = []
    seen = set()
    for key in fonts_used(build.repo, build.resolved, template):
        path = _mapped_font(build, key, key)
        entry = build.font_map[key]
        styles = families.setdefault(entry.family, [])
        if entry.style not in styles:
            styles.append(entry.style)
        if path not in seen:
            seen.add(path)
            paths.append(path)
    return families, sorted(paths)


def _mapped_font(build, key, element_id):
    entry = build.font_map.get(key)
    if entry is None:
        raise SpecError(f"{element_id}: {key} is not in the font map")
    path = _inside(Path(build.cfg.fonts), entry.file, "fonts folder")
    if not path.is_file():
        raise SpecError(f"missing file: fonts/{entry.file}")
    return path


def _read_font(path, element_id):
    try:
        return read_metrics(path)
    except (OSError, TTLibError, KeyError) as error:
        raise SpecError(f"{path}: {error}") from error


def _copy(build, element, block, element_id):
    slot = element.get("slot")
    if isinstance(slot, str) and slot and slot in build.resolved.slots:
        return slot_text(build.repo, build.resolved.series, build.resolved.slots[slot])
    characters = block.get("characters", "")
    if not isinstance(characters, str):
        raise SpecError(f"{element_id}: text.characters must be text")
    return characters


def _image_file(build, element, element_id):
    slot = element.get("slot")
    if isinstance(slot, str) and slot in build.resolved.slots:
        value = build.resolved.slots[slot]
        if not isinstance(value, dict):
            raise SpecError(f"{slot}: image slot must be a mapping")
        src = value.get("src")
        if not isinstance(src, str) or not src:
            raise SpecError(f"{slot}: src must be text")
        path = _inside(Path(build.cfg.photos), src, "photos folder")
        if not path.is_file():
            raise SpecError(f"missing file: photos/{src}")
        return path
    return _asset_file(build.repo, element, element_id)


def _asset_file(repo, element, element_id):
    asset = element.get("asset")
    if not isinstance(asset, str) or not asset:
        raise SpecError(f"{element_id}: asset must be text")
    path = _inside(Path(repo), asset, "repository")
    if not path.is_file():
        raise SpecError(f"missing file: {asset}")
    return path


def _raster_info(path):
    try:
        with Image.open(path) as image:
            image.load()
            width, height = image.size
            dpi = _dpi(image)
    except (OSError, UnidentifiedImageError) as error:
        raise SpecError(f"{path}: {error}") from error
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise SpecError(f"{path}: image size must be two positive numbers")
    return width, height, dpi


def _dpi(image) -> float:
    dpi = image.info.get("dpi", (72, 72))
    if isinstance(dpi, (tuple, list)) and dpi:
        dpi = dpi[0]
    if isinstance(dpi, (tuple, list)) and dpi:
        dpi = dpi[0]
    if not _number(dpi) or float(dpi) <= 0:
        return 72.0
    return float(dpi)


def _fit_focus(resolved, element, element_id):
    fit, focus = "fill", (0.5, 0.5)
    slot = element.get("slot")
    value = resolved.slots.get(slot) if isinstance(slot, str) else None
    if not isinstance(value, dict):
        return fit, focus
    chosen = value.get("fit", "fill")
    if not isinstance(chosen, str) or chosen not in _FITS:
        raise SpecError(f"{element_id}: fit must be fill, fit or none")
    raw = value.get("focus", (0.5, 0.5))
    if (
        not isinstance(raw, (list, tuple))
        or len(raw) != 2
        or any(not _number(item) or not 0 <= float(item) <= 1 for item in raw)
    ):
        raise SpecError(f"{element_id}: focus must be two numbers from 0 to 1")
    return chosen, (float(raw[0]), float(raw[1]))


def _pdf_size(path: Path) -> tuple[float, float]:
    try:
        result = subprocess.run(
            ["pdfinfo", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except OSError as error:
        raise SpecError(f"pdfinfo: {error}") from error
    except subprocess.TimeoutExpired as error:
        raise SpecError(f"pdfinfo: {path}: timed out") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise SpecError(f"pdfinfo: {path}: {detail or result.returncode}")
    if not isinstance(result.stdout, str):
        raise SpecError(f"pdfinfo: {path}: output must be text")
    match = _PAGE_SIZE.search(result.stdout)
    if match is None:
        raise SpecError(f"pdfinfo: {path}: no page size")
    return float(match.group(1)), float(match.group(2))


def _link_name(links: dict[str, Path], element_id: str, source: Path) -> str:
    resolved = source.resolve()
    for name, existing in links.items():
        if existing.resolve() == resolved:
            return name
    base = source.name
    if not _single_segment(base):
        raise SpecError(f"{base}: link name must stay inside links")
    name = base if base not in links else f"{element_id}-{base}"
    number = 2
    while name in links:
        name = f"{element_id}-{number}-{base}"
        number += 1
        if not _single_segment(name):
            raise SpecError(f"{name}: link name must stay inside links")
    links[name] = source
    return name


def _link_uri(build, name: str) -> str:
    home = str(build.cfg.mac_home).rstrip("/")
    series = build.resolved.series
    doc = build.resolved.doc
    path = f"{home}/makeready/jobs/{series}/{doc}/in/links/{name}"
    return "file:" + quote(path)


def _read_seed(package: zipfile.ZipFile, path: Path, name: str) -> bytes:
    try:
        return package.read(name)
    except (zipfile.BadZipFile, zlib.error, EOFError) as error:
        raise SpecError(f"{path}: {name}: {str(error) or 'member data ends early'}") from error


def _load_seed(path: Path) -> _Seed:
    if not path.is_file():
        raise SpecError(f"missing file: {path}")
    try:
        with zipfile.ZipFile(path) as package:
            names = set(package.namelist())

            def take(name: str) -> bytes:
                if name not in names:
                    raise SpecError(f"{path}: missing {name}")
                return _read_seed(package, path, name)

            designmap_xml = take("designmap.xml")
            aid = aid_instruction(designmap_xml)
            if aid is None:
                raise SpecError(
                    f"{path}: designmap.xml has no <?aid?> instruction; "
                    "InDesign cannot open a package built from it"
                )
            designmap = _parse(designmap_xml, "designmap.xml")
            spread_src, story_src, dropped = _seed_refs(designmap, package, path)
            spread_root = _parse(take(spread_src), spread_src)
            story_root = _parse(take(story_src), story_src)
            graphic = take("Resources/Graphic.xml")
            styles = take("Resources/Styles.xml")
            fonts = take("Resources/Fonts.xml")
            preferences = _parse(take("Resources/Preferences.xml"), "Resources/Preferences.xml")
    except SpecError:
        raise
    except (OSError, zipfile.BadZipFile, zlib.error, EOFError) as error:
        raise SpecError(f"{path}: {error}") from error
    spread = spread_root.find("Spread")
    if spread is None:
        raise SpecError(f"{spread_src}: no Spread")
    page = next(spread.iter("Page"), None)
    text_frame = next(spread.iter("TextFrame"), None)
    shape = next((item for item in spread.iter("Rectangle") if item.get("FillColor")), None)
    image_frame = next((item for item in spread.iter("Rectangle") if item.find("Image") is not None), None)
    pdf_frame = next((item for item in spread.iter("Rectangle") if item.find("PDF") is not None), None)
    story = story_root.find("Story")
    for label, element in (
        ("page", page),
        ("text frame", text_frame),
        ("shape", shape),
        ("image", image_frame),
        ("PDF", pdf_frame),
        ("story", story),
    ):
        if element is None:
            raise SpecError(f"{path}: no {label} to copy")
    return _Seed(
        path,
        designmap,
        preferences,
        graphic,
        styles,
        fonts,
        spread,
        page,
        story,
        text_frame,
        shape,
        next(spread.iter("Oval"), None),
        image_frame,
        pdf_frame,
        dropped,
        aid,
    )


def _seed_refs(designmap, package, path):
    spread_src = None
    story_src = None
    dropped: set[str] = set()
    for child in list(designmap):
        if child.tag == _SPREAD and spread_src is None:
            spread_src = _zip_src(child.get("src"))
        elif child.tag == _STORY:
            src = _zip_src(child.get("src"))
            if story_src is None:
                story_src = src
            if src.startswith("Stories/"):
                try:
                    data = _read_seed(package, path, src)
                except KeyError:
                    raise SpecError(f"{path}: missing {src}") from None
                for element in _parse(data, src).iter("Story"):
                    self_id = element.get("Self")
                    if isinstance(self_id, str) and self_id:
                        dropped.add(self_id)
    if spread_src is None:
        raise SpecError(f"{path}: designmap has no spread")
    if story_src is None:
        raise SpecError(f"{path}: designmap has no story")
    return spread_src, story_src, dropped


def _zip_src(src) -> str:
    if not isinstance(src, str) or not src or src.startswith("/") or "\\" in src:
        raise SpecError(f"designmap.xml: bad part {src!r}")
    if any(part in {"", ".", ".."} for part in src.split("/")):
        raise SpecError(f"designmap.xml: {src}: path must stay inside the package")
    return src


def _pages(template):
    if not isinstance(template, dict):
        raise SpecError("template must be a mapping")
    pages = template.get("pages")
    if not isinstance(pages, list) or not pages:
        raise SpecError("template: pages must be a list")
    checked = []
    for index, page in enumerate(pages):
        if not isinstance(page, dict):
            raise SpecError(f"template: pages[{index}] must be a mapping")
        elements = page.get("elements")
        if not isinstance(elements, list):
            raise SpecError(f"template: pages[{index}].elements must be a list")
        name = page.get("name")
        if not isinstance(name, str) or not name:
            raise SpecError(f"template: pages[{index}].name must be text")
        width, height = _page_size(page, index)
        checked.append((index, page, name, width, height))
    return checked


def _page_size(page, index):
    width, height = page.get("width"), page.get("height")
    if not _number(width) or not _number(height) or float(width) <= 0 or float(height) <= 0:
        raise SpecError(f"template: pages[{index}] width and height must be positive numbers")
    return float(width), float(height)


def _bleed(template) -> float:
    bleed = template.get("bleed", 0)
    if not _number(bleed) or float(bleed) < 0:
        raise SpecError("template: bleed must be a number from 0")
    return float(bleed)


def _brand_swatches(repo):
    path = Path(repo) / "brand" / "swatches.yaml"
    if not path.is_file():
        raise SpecError("missing file: brand/swatches.yaml")
    try:
        return load_swatches(path)
    except OSError as error:
        raise SpecError(f"{path}: {error}") from error
    except ValueError as error:
        raise SpecError(str(error)) from error


def _override_values(element, resolved) -> dict:
    overrides = resolved.overrides
    if not isinstance(overrides, dict):
        raise SpecError(f"{element.get('id')}: overrides must be a mapping")
    values = overrides.get(element.get("id"), {})
    if values is None:
        return {}
    if not isinstance(values, dict):
        raise SpecError(f"{element.get('id')}: overrides must be a mapping")
    return values


def _text_numbers(block, values, element_id):
    return (
        _text_number(block, values, "size", element_id),
        _text_number(block, values, "tracking", element_id),
        _text_number(block, values, "leading", element_id),
    )


def _text_number(block, values, key, element_id) -> float:
    value = values[key] if key in values else block.get(key)
    if not _number(value):
        raise SpecError(f"{element_id}: text.{key} must be a number")
    return float(value)


def _font_name(font, element_id) -> tuple[str, str]:
    if not isinstance(font, str) or font.count("/") != 1:
        raise SpecError(f"{element_id}: text.font must be Family/Style")
    family, style = font.split("/", 1)
    if not family or not style:
        raise SpecError(f"{element_id}: text.font must be Family/Style")
    return family, style


def _fill_swatch(element, values, element_id):
    if "swatch" in values:
        swatch = values["swatch"]
        if not isinstance(swatch, str) or not swatch:
            raise SpecError(f"{element_id}: swatch must be text")
        return swatch
    fill = element.get("fill")
    if not isinstance(fill, dict):
        return None
    swatch = fill.get("swatch")
    if swatch is None:
        return None
    if not isinstance(swatch, str) or not swatch:
        raise SpecError(f"{element_id}: fill swatch must be text")
    return swatch


def _fill_opacity(element, element_id) -> float:
    fill = element.get("fill")
    if not isinstance(fill, dict) or "opacity" not in fill:
        return 1.0
    opacity = fill.get("opacity")
    if not _number(opacity) or not 0 <= float(opacity) <= 1:
        raise SpecError(f"{element_id}: fill opacity must be a number from 0 to 1")
    return float(opacity)


def _stroke(element, element_id):
    stroke = element.get("stroke")
    if stroke is None:
        return None, 0.0, 1.0
    if not isinstance(stroke, dict):
        raise SpecError(f"{element_id}: stroke must be a mapping")
    swatch = stroke.get("swatch")
    if swatch is not None and (not isinstance(swatch, str) or not swatch):
        raise SpecError(f"{element_id}: stroke swatch must be text")
    weight = stroke.get("weight", 0)
    if not _number(weight) or float(weight) < 0:
        raise SpecError(f"{element_id}: stroke weight must be a number from 0")
    opacity = stroke.get("opacity", 1)
    if not _number(opacity) or not 0 <= float(opacity) <= 1:
        raise SpecError(f"{element_id}: stroke opacity must be a number from 0 to 1")
    return (swatch if isinstance(swatch, str) else None), float(weight), float(opacity)


def _object_opacity(element, values, element_id) -> float:
    opacity = values["opacity"] if "opacity" in values else element.get("opacity", 1)
    if not _number(opacity) or not 0 <= float(opacity) <= 1:
        raise SpecError(f"{element_id}: opacity must be a number from 0 to 1")
    return float(opacity)


def _swatch_ref(name) -> str:
    return "Swatch/None" if not name else f"Color/{name}"


def _element_id(element, index) -> str:
    element_id = element.get("id")
    if not isinstance(element_id, str) or not element_id:
        raise SpecError(f"template: pages[{index}] element id must be text")
    return element_id


def _hidden(element, overrides) -> bool:
    if not isinstance(overrides, dict):
        return False
    values = overrides.get(element.get("id"))
    return isinstance(values, dict) and values.get("hidden") is True


def _percent(opacity: float) -> str | None:
    value = round(float(opacity) * 100, 4)
    if value >= 100:
        return None
    return _num(value)


def _spread_matrix(page_w, page_h, transform) -> str:
    matrix = compose(translate(-page_w / 2, -page_h / 2), tuple(float(item) for item in transform))
    return _matrix(matrix)


def _matrix(values) -> str:
    return " ".join(_num(item) for item in values)


def _pair(point) -> str:
    return f"{_num(point[0])} {_num(point[1])}"


def _num(value) -> str:
    text = f"{float(value):.10f}".rstrip("0").rstrip(".")
    if text in {"", "-0"}:
        return "0"
    return text


def _single_segment(name: str) -> bool:
    return bool(name) and name not in {".", ".."} and "/" not in name and "\\" not in name and "\x00" not in name


def _inside(root: Path, relative: str, where: str) -> Path:
    if not isinstance(relative, str) or not _relative(relative):
        raise SpecError(f"{relative}: path must stay inside the {where}")
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise SpecError(f"{relative}: path must stay inside the {where}")
    return root / relative


def _relative(value: str) -> bool:
    if not value or "\\" in value or "\x00" in value:
        return False
    path = Path(value)
    if path.is_absolute():
        return False
    return all(part not in {"", ".", ".."} for part in path.parts)


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _reidentify(element, ids: _Ids) -> None:
    for node in element.iter():
        if node.get("Self"):
            node.set("Self", ids.next())


def _clone(element: ET.Element) -> ET.Element:
    return ET.fromstring(ET.tostring(element, encoding="unicode"))


def _parse(xml: bytes, part: str) -> ET.Element:
    try:
        return ET.fromstring(xml)
    except ET.ParseError as error:
        raise SpecError(f"{part}: {error}") from error


def _xml(root: ET.Element) -> bytes:
    body = ET.tostring(root, encoding="unicode")
    return f"{_DECL}\n{body}\n".encode("utf-8")
