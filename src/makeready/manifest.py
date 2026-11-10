"""Content-addressed manifest of every input a document build reads."""

import hashlib
import json
from pathlib import Path

from makeready.fontmap import fonts_used, load_font_map
from makeready.spec import SpecError, _elements, hooks_file

_BRAND = ("swatches.yaml", "font-map.yaml", "asset-map.yaml")
_CONFIG = ("config:print", "config:mac")


def compute(repo, cfg, resolved, template) -> dict[str, str]:
    repo = Path(repo)
    series = _segment(resolved.series, "series")
    doc = _segment(resolved.doc, "document")
    template_name = _segment(resolved.template, "template")
    manifest: dict[str, str] = {}
    origins: dict[str, Path] = {}

    document = repo / "series" / series / f"{doc}.yaml"
    if document.is_file():
        _add(manifest, origins, f"series/{series}/{doc}.yaml", document)
    series_file = repo / "series" / series / "series.yaml"
    if series_file.is_file():
        _add(manifest, origins, f"series/{series}/series.yaml", series_file)
    for key, path in _copy_files(repo, resolved, template, series):
        _add(manifest, origins, key, path)

    _add(manifest, origins, f"templates/{template_name}/template.json", repo / "templates" / template_name / "template.json")
    overrides = repo / "templates" / template_name / "template-overrides.yaml"
    if overrides.is_file():
        _add(manifest, origins, f"templates/{template_name}/template-overrides.yaml", overrides)
    hooks = hooks_file(repo, template_name, template)
    if hooks is not None:
        _add(manifest, origins, f"templates/{template_name}/{hooks.name}", hooks)

    for name in _BRAND:
        path = repo / "brand" / name
        if path.is_file():
            _add(manifest, origins, f"brand/{name}", path)
    for element in _elements(template):
        asset = element.get("asset")
        if asset is None:
            continue
        if not isinstance(asset, str) or not _relative(asset):
            raise SpecError(f"{element.get('id')}: asset must stay inside the repository")
        _add(manifest, origins, asset, _inside(repo, asset, "repository"))

    photos = Path(cfg.photos)
    for src in _photo_sources(resolved, template):
        _add(manifest, origins, f"photos/{src}", _inside(photos, src, "photos folder"))

    font_map = load_font_map(repo)
    fonts = Path(cfg.fonts)
    for name in fonts_used(repo, resolved, template):
        entry = font_map.get(name)
        if entry is None:
            continue
        _add(manifest, origins, f"fonts/{entry.file}", _inside(fonts, entry.file, "fonts folder"))

    _add(manifest, origins, "seed/reference.idml", repo / "seed" / "reference.idml")
    mac = repo / "mac"
    if not mac.is_dir():
        raise SpecError("missing file: mac")
    mac_root = mac.resolve()
    for path in sorted(item for item in mac.rglob("*") if item.is_file()):
        relative = path.relative_to(mac).as_posix()
        if not _relative(relative) or not path.resolve().is_relative_to(mac_root):
            raise SpecError(f"mac/{relative}: path must stay inside mac/")
        _add(manifest, origins, f"mac/{relative}", path)

    manifest["config:print"] = _digest(
        {"profile": cfg.print_profile, "inks": list(cfg.print_inks)}
    )
    manifest["config:mac"] = _digest({"home": cfg.mac_home})
    return manifest


def build_id(manifest) -> str:
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()[:16]


def _copy_files(repo: Path, resolved, template: dict, series: str):
    seen: set[str] = set()
    root = repo / "series" / series
    for element in _elements(template):
        if element.get("kind") != "text":
            continue
        slot = element.get("slot")
        if not isinstance(slot, str) or slot not in resolved.slots:
            continue
        value = resolved.slots[slot]
        if not isinstance(value, str) or not value.endswith((".md", ".txt")) or value in seen:
            continue
        seen.add(value)
        if not _relative(value):
            raise SpecError(f"{value}: path must stay inside the series folder")
        yield f"series/{series}/{value}", _inside(root, value, "series folder")


def _photo_sources(resolved, template: dict):
    seen: set[str] = set()
    for element in _elements(template):
        if element.get("kind") != "image":
            continue
        slot = element.get("slot")
        if not isinstance(slot, str) or not slot:
            continue
        if slot not in resolved.slots:
            raise SpecError(f"missing slot {slot}")
        value = resolved.slots[slot]
        if not isinstance(value, dict):
            raise SpecError(f"{slot}: image slot must be a mapping")
        src = value.get("src")
        if not isinstance(src, str) or not _relative(src):
            raise SpecError(f"{slot}: src must stay inside the photos folder")
        if src not in seen:
            seen.add(src)
            yield src


def _add(manifest: dict[str, str], origins: dict[str, Path], key: str, path: Path) -> None:
    if key in _CONFIG:
        raise SpecError(f"{key}: name is reserved for the build configuration")
    if key in manifest:
        previous = origins[key]
        if previous.resolve() == path.resolve():
            return
        raise SpecError(f"{key}: {previous} and {path}")
    if not path.is_file():
        raise SpecError(f"missing file: {key}")
    try:
        manifest[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise SpecError(f"{key}: {error}") from error
    origins[key] = path


def _digest(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _segment(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or value in ("", ".", "..")
        or "\\" in value
        or "/" in value
        or value != Path(value).name
    ):
        raise SpecError(f"{label} must be a single path segment")
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
