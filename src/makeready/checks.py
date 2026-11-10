"""Pre-render checks: files, resolution, swatches, inks, bleed and fonts."""

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageCms, UnidentifiedImageError

from makeready.colour import load_swatches
from makeready.crop import crop
from makeready.fontmap import fonts_used, load_font_map
from makeready.geometry import bbox, corners
from makeready.spec import SpecError

_PROCESS = ("C", "M", "Y", "K")
_KINDS = {"text", "image", "shape", "vector"}
_VECTOR_ASSETS = {".pdf", ".ai"}
_RASTER = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".gif", ".webp", ".bmp"}
_SIZE_KEYS = ("x", "y", "width", "height")


@dataclass(frozen=True)
class Finding:
    level: str
    code: str
    element: str | None
    message: str


def run(repo, cfg, resolved, template) -> list[Finding]:
    repo = Path(repo)
    findings: list[Finding] = []
    swatches = _swatch_index(repo)
    spots: set[str] = set()
    for index, page in _pages(template):
        page_w, page_h = _page_size(page, index)
        for element in page["elements"]:
            if not isinstance(element, dict):
                raise SpecError(f"template: pages[{index}] elements must be mappings")
            element_id = _element_id(element, index)
            if _hidden(element, resolved.overrides):
                continue
            kind = element.get("kind")
            if not isinstance(kind, str) or kind not in _KINDS:
                raise SpecError(f"{element_id}: kind must be text, image, shape or vector")
            placed = place(element, resolved.overrides)
            if kind == "text":
                _missing_copy(findings, repo, resolved, element, element_id)
            _asset(findings, repo, cfg, resolved, element, element_id, kind, placed)
            if kind == "image":
                _photo(findings, cfg, resolved, element, element_id, placed)
            for name in _swatch_names(element, resolved.overrides):
                swatch = swatches.get(name)
                if swatch is None:
                    findings.append(Finding("error", "unknown-swatch", element_id, f"unknown swatch {name}"))
                elif swatch.spot:
                    spots.add(name)
            if kind != "text" and element.get("nonprinting") is not True:
                _bleed(findings, template, element_id, placed, page_w, page_h)
    _inks(findings, spots, cfg)
    _fonts(findings, repo, cfg, resolved, template)
    return findings


def place(element, overrides) -> dict:
    element_id = element.get("id", "element")
    transform = element.get("transform")
    if (
        not isinstance(transform, (list, tuple))
        or len(transform) != 6
        or any(not _number(item) for item in transform)
    ):
        raise SpecError(f"{element_id}: transform must be six numbers")
    width = element.get("width")
    height = element.get("height")
    if not _number(width) or not _number(height) or float(width) <= 0 or float(height) <= 0:
        raise SpecError(f"{element_id}: width and height must be positive numbers")
    values = _values(element, overrides)
    placed = [float(item) for item in transform]
    for axis, key in ((4, "x"), (5, "y")):
        if key not in values:
            continue
        amount = values[key]
        if not _number(amount):
            raise SpecError(f"{element_id}: {key} must be a number")
        placed[axis] += float(amount)
    if "width" in values:
        width = _positive(element_id, "width", values["width"])
    if "height" in values:
        height = _positive(element_id, "height", values["height"])
    result = dict(element)
    result["transform"] = placed
    result["width"] = float(width)
    result["height"] = float(height)
    return result


def _missing_copy(findings, repo: Path, resolved, element: dict, element_id: str) -> None:
    slot = element.get("slot")
    if not isinstance(slot, str) or slot not in resolved.slots:
        return
    value = resolved.slots[slot]
    if not isinstance(value, str) or not value.endswith((".md", ".txt")):
        return
    path = _inside(repo / "series" / _segment(resolved.series), value, "series folder")
    if not path.is_file():
        findings.append(Finding("error", "missing-file", element_id, f"missing file: series/{resolved.series}/{value}"))


def _asset(findings, repo, cfg, resolved, element, element_id, kind, placed) -> None:
    asset = element.get("asset")
    if not isinstance(asset, str) or not asset:
        return
    suffix = Path(asset).suffix.lower()
    if kind == "vector" and suffix not in _VECTOR_ASSETS:
        findings.append(Finding("error", "unsupported-asset", element_id, f"unsupported asset {asset}"))
    path = _inside(repo, asset, "repository")
    if not path.is_file():
        findings.append(Finding("error", "missing-file", element_id, f"missing file: {asset}"))
    elif kind == "image" and suffix in _RASTER:
        _raster(findings, cfg, resolved, element, element_id, placed, path)


def _photo(findings, cfg, resolved, element, element_id, placed) -> None:
    slot = element.get("slot")
    if not isinstance(slot, str) or not slot:
        return
    if slot not in resolved.slots:
        raise SpecError(f"missing slot {slot}")
    value = resolved.slots[slot]
    if not isinstance(value, dict):
        raise SpecError(f"{slot}: image slot must be a mapping")
    src = value.get("src")
    if not isinstance(src, str) or not src:
        raise SpecError(f"{slot}: src must be text")
    path = _inside(Path(cfg.photos), src, "photos folder")
    if not path.is_file():
        findings.append(Finding("error", "missing-file", element_id, f"missing file: photos/{src}"))
        return
    _raster(findings, cfg, resolved, element, element_id, placed, path)


def _raster(findings, cfg, resolved, element, element_id, placed, path: Path) -> None:
    try:
        with Image.open(path) as image:
            image.load()
            mode = image.mode
            size = image.size
            dpi = _dpi(image)
            profile = image.info.get("icc_profile")
    except (OSError, UnidentifiedImageError) as error:
        raise SpecError(f"{path}: {error}") from error
    img_w = size[0] * 72 / dpi
    img_h = size[1] * 72 / dpi
    fit, focus = _fit_focus(resolved, element)
    try:
        scale = crop(placed["width"], placed["height"], img_w, img_h, fit, focus).scale
    except ValueError as error:
        raise SpecError(f"{element_id}: {error}") from error
    if scale <= 0:
        raise SpecError(f"{element_id}: image scale must be positive")
    effective = dpi / scale
    if effective < 300:
        findings.append(
            Finding(
                "warning",
                "low-resolution",
                element_id,
                f"effective resolution {round(effective)} ppi at placed size",
            )
        )
    if mode == "CMYK" and profile:
        _profile(findings, element_id, profile, cfg.print_profile)


def _profile(findings, element_id: str, icc: object, print_profile: str) -> None:
    if not isinstance(icc, bytes) or not print_profile:
        return
    try:
        description = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(BytesIO(icc)))
    except (OSError, TypeError, ImageCms.PyCMSError) as error:
        raise SpecError(f"{element_id}: {error}") from error
    desc = description.replace("\x00", "").strip()
    folded = desc.casefold()
    wanted = print_profile.casefold()
    if folded and (wanted in folded or folded in wanted):
        return
    findings.append(
        Finding(
            "warning",
            "cmyk-profile",
            element_id,
            f"CMYK profile {desc or 'unnamed'} does not match {print_profile}",
        )
    )


def _fit_focus(resolved, element: dict) -> tuple[str, tuple[float, float]]:
    fit = "fill"
    focus = (0.5, 0.5)
    slot = element.get("slot")
    value = resolved.slots.get(slot) if isinstance(slot, str) else None
    if not isinstance(value, dict):
        return fit, focus
    chosen = value.get("fit", "fill")
    if isinstance(chosen, str):
        fit = chosen
    raw = value.get("focus")
    if isinstance(raw, (list, tuple)) and len(raw) == 2 and all(_number(item) for item in raw):
        focus = (float(raw[0]), float(raw[1]))
    return fit, focus


def _bleed(findings, template: dict, element_id: str, placed: dict, page_w: float, page_h: float) -> None:
    bleed = _bleed_amount(template)
    x0, y0, x1, y1 = bbox(corners(tuple(placed["transform"]), placed["width"], placed["height"]))
    sides = []
    if x0 <= 1 and x0 > -bleed + 0.5:
        sides.append("left")
    if y0 <= 1 and y0 > -bleed + 0.5:
        sides.append("top")
    if x1 >= page_w - 1 and x1 < page_w + bleed - 0.5:
        sides.append("right")
    if y1 >= page_h - 1 and y1 < page_h + bleed - 0.5:
        sides.append("bottom")
    if sides:
        findings.append(
            Finding(
                "warning",
                "bleed",
                element_id,
                f"does not reach the bleed on the {', '.join(sides)}",
            )
        )


def _inks(findings, spots: set[str], cfg) -> None:
    used = set(_PROCESS) | spots
    configured = set(cfg.print_inks)
    if used == configured:
        return
    parts = []
    missing = sorted(used - configured)
    extra = sorted(configured - used)
    if missing:
        parts.append("missing from print.inks: " + ", ".join(missing))
    if extra:
        parts.append("not used: " + ", ".join(extra))
    findings.append(Finding("error", "ink-mismatch", None, "; ".join(parts)))


def _fonts(findings, repo, cfg, resolved, template) -> None:
    mapped = load_font_map(repo)
    fonts = Path(cfg.fonts)
    for name in fonts_used(repo, resolved, template):
        entry = mapped.get(name)
        if entry is None:
            findings.append(Finding("error", "font-unmapped", name, f"{name} is not in the font map"))
            continue
        path = _inside(fonts, entry.file, "fonts folder")
        if not path.is_file():
            findings.append(Finding("error", "missing-font", name, f"missing file: fonts/{entry.file}"))


def _swatch_index(repo: Path) -> dict:
    path = repo / "brand" / "swatches.yaml"
    if not path.is_file():
        raise SpecError("missing file: brand/swatches.yaml")
    try:
        swatches = load_swatches(path)
    except OSError as error:
        raise SpecError(f"brand/swatches.yaml: {error}") from error
    except ValueError as error:
        raise SpecError(str(error)) from error
    return {swatch.name: swatch for swatch in swatches}


def _swatch_names(element: dict, overrides: dict) -> list[str]:
    values = _values(element, overrides)
    names = []
    if "swatch" in values:
        swatch = values.get("swatch")
        if isinstance(swatch, str) and swatch:
            names.append(swatch)
    else:
        names.extend(_paint_swatch(element.get("fill")))
    names.extend(_paint_swatch(element.get("stroke")))
    return list(dict.fromkeys(names))


def _paint_swatch(paint: object) -> list[str]:
    if not isinstance(paint, dict):
        return []
    swatch = paint.get("swatch")
    if isinstance(swatch, str) and swatch:
        return [swatch]
    return []


def _pages(template: dict):
    pages = template.get("pages")
    if not isinstance(pages, list):
        raise SpecError("template: pages must be a list")
    bleed = template.get("bleed", 0)
    if not _number(bleed) or float(bleed) < 0:
        raise SpecError("template: bleed must be a number from 0")
    for index, page in enumerate(pages):
        if not isinstance(page, dict):
            raise SpecError(f"template: pages[{index}] must be a mapping")
        elements = page.get("elements")
        if not isinstance(elements, list):
            raise SpecError(f"template: pages[{index}].elements must be a list")
        yield index, page


def _page_size(page: dict, index: int) -> tuple[float, float]:
    width, height = page.get("width"), page.get("height")
    if not _number(width) or not _number(height) or float(width) <= 0 or float(height) <= 0:
        raise SpecError(f"template: pages[{index}] width and height must be positive numbers")
    return float(width), float(height)


def _bleed_amount(template: dict) -> float:
    bleed = template.get("bleed", 0)
    if not _number(bleed) or float(bleed) < 0:
        raise SpecError("template: bleed must be a number from 0")
    return float(bleed)


def _values(element: dict, overrides: dict) -> dict:
    if not isinstance(overrides, dict):
        raise SpecError(f"{element.get('id')}: overrides must be a mapping")
    values = overrides.get(element.get("id"))
    if isinstance(values, dict):
        return values
    if any(key in overrides for key in _SIZE_KEYS):
        return overrides
    return {}


def _hidden(element: dict, overrides: dict) -> bool:
    if not isinstance(overrides, dict):
        return False
    values = overrides.get(element.get("id"))
    return isinstance(values, dict) and values.get("hidden") is True


def _element_id(element: dict, index: int) -> str:
    element_id = element.get("id")
    if not isinstance(element_id, str) or not element_id:
        raise SpecError(f"template: pages[{index}] element id must be text")
    return element_id


def _dpi(image) -> float:
    dpi = image.info.get("dpi", (72, 72))
    if isinstance(dpi, (tuple, list)) and dpi:
        dpi = dpi[0]
    if isinstance(dpi, (tuple, list)) and dpi:
        dpi = dpi[0]
    if not _number(dpi) or float(dpi) <= 0:
        return 72.0
    return float(dpi)


def _positive(element_id: str, key: str, value: object) -> float:
    if not _number(value) or float(value) <= 0:
        raise SpecError(f"{element_id}: {key} must be a positive number")
    return float(value)


def _segment(value: object) -> str:
    if not isinstance(value, str) or value in ("", ".", "..") or "/" in value or "\\" in value:
        raise SpecError(f"{value}: series and document names are lowercase letters, digits and hyphens")
    return value


def _relative(value: str) -> bool:
    if not value or "\\" in value or "\x00" in value:
        return False
    path = Path(value)
    if path.is_absolute():
        return False
    return all(part not in ("", ".", "..") for part in path.parts)


def _inside(root: Path, relative: str, where: str) -> Path:
    if not _relative(relative):
        raise SpecError(f"{relative}: path must stay inside the {where}")
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise SpecError(f"{relative}: path must stay inside the {where}")
    return root / relative


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
