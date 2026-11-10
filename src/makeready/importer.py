"""Write a Figma file into a template directory."""

import json
import os
import shutil
from pathlib import Path

import yaml

from makeready.colour import load_swatches
from makeready.extract import _page_file_name, extract
from makeready.figma import FigmaError, cached_file

_PNG = b"\x89PNG\r\n\x1a\n"


def load_asset_map(repo: Path) -> dict[str, str]:
    path = Path(repo) / "brand" / "asset-map.yaml"
    if not path.is_file():
        return {}
    data = _read_yaml(path)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: assets must be a mapping")
    assets = data.get("assets")
    if not isinstance(assets, dict):
        raise ValueError(f"{path}: assets must be a mapping")
    mapped: dict[str, str] = {}
    for element_id, asset in assets.items():
        if not isinstance(element_id, str) or not element_id or not isinstance(asset, str) or not asset:
            raise ValueError(f"{path}: asset names and paths must be text")
        mapped[element_id] = asset
    return mapped


def import_figma(
    repo: Path,
    client,
    key: str,
    name: str,
    frames: list[str] | None = None,
    version: str | None = None,
    scale: float = 1.0,
) -> list[str]:
    repo = Path(repo)
    template_dir = _template_dir(repo, name)
    swatches = _swatches(repo / "brand" / "swatches.yaml")
    asset_map = load_asset_map(repo)
    previous = template_dir / "template.json"
    old_template = _read_json(previous) if previous.is_file() else {}
    old_slots = _slots(old_template, previous) if previous.is_file() else set()
    # Every request and download lands here. The live template is replaced
    # only after they all succeed. This directory is removed unless it holds
    # the previous template's files after a failed restore.
    staging = _staging_dir(template_dir)
    keep = False
    try:
        doc = _cached_doc(client, key, template_dir / ".figma-cache", staging / ".figma-cache", version)
        file_version = _file_version(doc, key)
        template, report = extract(doc, name, swatches, frames, scale)
        template["figma"] = {"key": key, "version": file_version}
        template.update(_hand_set(old_template))
        report.extend(_removed(repo, name, old_slots - _slots(template, previous)))
        asset_names: set[str] = set()
        _render_references(client, key, file_version, template, staging)
        _place_vectors(client, key, file_version, name, template, staging, asset_map, report, asset_names)
        _place_images(client, key, name, template, staging, asset_map, asset_names)
        _write_outputs(staging, template, report)
        _publish(template_dir, staging)
    except _KeptPrevious:
        keep = True
        raise
    finally:
        if not keep:
            shutil.rmtree(staging, ignore_errors=True)
    return report


# Top-level keys an import derives from Figma or its own arguments. Every other key in
# the previous template.json (hooks, a hand-set bleed) was set by hand and survives.
_IMPORTED_KEYS = frozenset({"name", "scale", "pages", "figma"})


def _hand_set(old_template: dict) -> dict:
    return {k: v for k, v in old_template.items() if k not in _IMPORTED_KEYS}


class _KeptPrevious(ValueError):
    """Restoring the previous template failed; its files stay in the staging directory."""


def _staging_dir(template_dir: Path) -> Path:
    root = template_dir.parent
    root.mkdir(parents=True, exist_ok=True)
    staging = root / f".{template_dir.name}.import-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    return staging


def _cached_doc(client, key: str, live_cache: Path, stage_cache: Path, version: str | None):
    if _cache_hit(live_cache, version):
        return cached_file(client, key, live_cache, version)
    return cached_file(client, key, stage_cache, version)


def _cache_hit(cache_dir: Path, version: str | None) -> bool:
    if not isinstance(version, str) or not version or version in {".", ".."}:
        return False
    if "/" in version or "\\" in version:
        return False
    return (cache_dir / f"{version}.json").is_file()


def _publish(template_dir: Path, staging: Path) -> None:
    # The live reference/, assets/ and report.txt are set aside, not deleted,
    # until the new template.json is in place, so a failure puts them back.
    # A path that was not there before is removed. Files move in with
    # os.replace, so none is ever left half written.
    aside = staging / ".previous"
    fresh: list[Path] = []
    try:
        template_dir.mkdir(parents=True, exist_ok=True)
        _install_cache(template_dir / ".figma-cache", staging / ".figma-cache")
        aside.mkdir()
        try:
            for part in ("reference", "assets"):
                dest = template_dir / part
                if not dest.exists():
                    fresh.append(dest)
                _replace_tree(dest, staging / part, aside / part)
            report = template_dir / "report.txt"
            if report.is_file():
                shutil.copy2(report, aside / "report.txt")
            else:
                fresh.append(report)
            for filename in ("report.txt", "template.json"):
                os.replace(staging / filename, template_dir / filename)
        except OSError as error:
            try:
                for part in ("reference", "assets"):
                    _restore_tree(template_dir / part, aside / part)
                if (aside / "report.txt").is_file():
                    os.replace(aside / "report.txt", template_dir / "report.txt")
                for path in fresh:
                    _remove_installed(path)
            except OSError as restore_error:
                raise _KeptPrevious(
                    f"{template_dir}: {error}; restoring the previous template failed "
                    f"({restore_error}); its files are kept in {aside}"
                ) from restore_error
            raise
    except OSError as error:
        raise ValueError(f"{template_dir}: {error}") from error


def _remove_installed(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.is_file():
        path.unlink()


def _install_cache(dest: Path, src: Path) -> None:
    if not src.is_dir():
        return
    dest.mkdir(parents=True, exist_ok=True)
    for path in sorted(src.iterdir()):
        if not path.is_file():
            continue
        target = dest / path.name
        data = path.read_bytes()
        if target.is_file() and target.read_bytes() == data:
            continue
        os.replace(path, target)


def _replace_tree(dest: Path, src: Path, aside: Path) -> None:
    if dest.exists():
        shutil.move(str(dest), str(aside))
    if src.exists():
        shutil.move(str(src), str(dest))


def _restore_tree(dest: Path, aside: Path) -> None:
    if not aside.exists():
        return
    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(aside), str(dest))


def _template_dir(repo: Path, name: str) -> Path:
    if not isinstance(name, str) or not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError(f"{name}: template name must be a single path segment")
    return repo / "templates" / name


def _swatches(path: Path):
    try:
        return load_swatches(path)
    except OSError as error:
        raise ValueError(f"{path}: {error}") from error


def _file_version(doc: object, key: str) -> str:
    if not isinstance(doc, dict):
        raise FigmaError(f"Figma file {key}: response must be a JSON object")
    version = doc.get("version")
    if not isinstance(version, str) or not version:
        raise FigmaError(f"Figma file {key}: version must be text")
    return version


def _slots(template: dict, source: Path) -> set[str]:
    pages = template.get("pages")
    if not isinstance(pages, list):
        raise ValueError(f"{source}: pages must be a list")
    slots: set[str] = set()
    for page in pages:
        if not isinstance(page, dict):
            raise ValueError(f"{source}: page must be an object")
        elements = page.get("elements", [])
        if not isinstance(elements, list):
            raise ValueError(f"{source}: elements must be a list")
        for element in elements:
            if not isinstance(element, dict):
                raise ValueError(f"{source}: element must be an object")
            slot = element.get("slot")
            if slot is None:
                continue
            if not isinstance(slot, str) or not slot:
                raise ValueError(f"{source}: slot must be text")
            slots.add(slot)
    return slots


def _removed(repo: Path, name: str, removed: set[str]) -> list[str]:
    if not removed:
        return []
    series_root = repo / "series"
    if not series_root.is_dir():
        return []
    lines: list[str] = []
    for series_dir in sorted(path for path in series_root.iterdir() if path.is_dir()):
        series_path = series_dir / "series.yaml"
        series_data: dict = {}
        series_template = None
        if series_path.is_file():
            series_data = _mapping(_read_yaml(series_path), series_path)
            series_template = _template_name(series_data, series_path)
            if series_template == name:
                lines.extend(_slot_lines(repo, series_path, series_data, removed))
        for doc_path in sorted(series_dir.glob("*.yaml")):
            if doc_path.name == "series.yaml":
                continue
            doc_data = _mapping(_read_yaml(doc_path), doc_path)
            doc_template = _template_name(doc_data, doc_path)
            if doc_template is None:
                doc_template = series_template
            if doc_template == name:
                lines.extend(_slot_lines(repo, doc_path, doc_data, removed))
    return lines


def _template_name(data: dict, path: Path) -> str | None:
    if "template" not in data:
        return None
    name = data["template"]
    if not isinstance(name, str) or not name:
        raise ValueError(f"{path}: template must be text")
    return name


def _slot_lines(repo: Path, path: Path, data: dict, removed: set[str]) -> list[str]:
    named = _named_slots(data, path)
    relative = path.relative_to(repo).as_posix()
    return [f"error: {relative} uses removed slot {slot}" for slot in sorted(removed) if slot in named]


def _named_slots(data: dict, path: Path) -> set[str]:
    named = set(_slot_keys(data.get("slots"), path, "slots"))
    defaults = data.get("defaults")
    if defaults is not None:
        if not isinstance(defaults, dict):
            raise ValueError(f"{path}: defaults must be a mapping")
        named.update(_slot_keys(defaults.get("slots"), path, "defaults.slots"))
    variants = data.get("variants")
    if variants is not None:
        if not isinstance(variants, list):
            raise ValueError(f"{path}: variants must be a list")
        for index, variant in enumerate(variants):
            if not isinstance(variant, dict):
                raise ValueError(f"{path}: variants[{index}] must be a mapping")
            named.update(_slot_keys(variant.get("slots"), path, f"variants[{index}].slots"))
    return named


def _slot_keys(slots: object, path: Path, label: str) -> set[str]:
    if slots is None:
        return set()
    if not isinstance(slots, dict):
        raise ValueError(f"{path}: {label} must be a mapping")
    keys: set[str] = set()
    for key in slots:
        if not isinstance(key, str) or not key:
            raise ValueError(f"{path}: {label} names must be text")
        keys.add(key)
    return keys


def _render_references(client, key: str, version: str, template: dict, template_dir: Path) -> None:
    pages = [page for page in template["pages"] if isinstance(page, dict)]
    if not pages:
        return
    # The proof looks this file up by the page's exact name.
    file_names = [_page_file_name(page.get("name"), _frame_label(page)) + ".png" for page in pages]
    ids = [_text(page, "figma_id", "page figma_id") for page in pages]
    urls = _url_map(client.render(key, ids, "png", version), "Figma render")
    for file_name, node_id in zip(file_names, ids, strict=True):
        dest = template_dir / "reference" / file_name
        client.download(_url(urls, node_id, "Figma render"), dest)


def _place_vectors(
    client,
    key: str,
    version: str,
    name: str,
    template: dict,
    template_dir: Path,
    asset_map: dict[str, str],
    report: list[str],
    asset_names: set[str],
) -> None:
    pending = []
    for element in _elements(template):
        if element.get("kind") != "vector":
            continue
        mapped = asset_map.get(_text(element, "id", "element id"))
        if mapped:
            element["asset"] = mapped
        else:
            pending.append(element)
    if not pending:
        return
    ids = [_text(element, "figma_id", "element figma_id") for element in pending]
    urls = _url_map(client.render(key, ids, "pdf", version), "Figma render")
    for element, node_id in zip(pending, ids, strict=True):
        element_id = element["id"]
        file_name = _asset_file_name(element_id, ".pdf", asset_names)
        dest = template_dir / "assets" / file_name
        client.download(_url(urls, node_id, "Figma render"), dest)
        element["asset"] = f"templates/{name}/assets/{file_name}"
        report.append(
            f"warning: {element_id}: vector exported from Figma as RGB PDF; "
            "map a print-ready file in brand/asset-map.yaml"
        )


def _place_images(
    client,
    key: str,
    name: str,
    template: dict,
    template_dir: Path,
    asset_map: dict[str, str],
    asset_names: set[str],
) -> None:
    pending = []
    for element in _elements(template):
        if element.get("kind") != "image" or element.get("slot"):
            continue
        mapped = asset_map.get(_text(element, "id", "element id"))
        if mapped:
            element["asset"] = mapped
        else:
            pending.append(element)
    if not pending:
        return
    fills = _url_map(client.image_fills(key), "Figma image fills")
    for index, element in enumerate(pending):
        image = element.get("image")
        if not isinstance(image, dict):
            raise ValueError(f"{element.get('id')}: image must be an object")
        ref = image.get("ref")
        if not isinstance(ref, str) or not ref:
            raise ValueError(f"{element.get('id')}: image ref must be text")
        element_id = _text(element, "id", "element id")
        raw = template_dir / "assets" / f".download-{index}"
        client.download(_url(fills, ref, "Figma image fills"), raw)
        ext = ".png" if _starts_with_png(raw) else ".jpg"
        file_name = _asset_file_name(element_id, ext, asset_names)
        final = template_dir / "assets" / file_name
        try:
            raw.rename(final)
        except OSError as error:
            raise ValueError(f"{final}: {error}") from error
        element["asset"] = f"templates/{name}/assets/{file_name}"


def _asset_file_name(element_id: str, ext: str, used: set[str]) -> str:
    stem = element_id.replace("/", "-").replace("\\", "-")
    candidate = f"{stem}{ext}"
    if candidate not in used:
        used.add(candidate)
        return candidate
    number = 2
    while f"{stem}-{number}{ext}" in used:
        number += 1
    candidate = f"{stem}-{number}{ext}"
    used.add(candidate)
    return candidate


def _frame_label(page: dict) -> str:
    figma_id = page.get("figma_id")
    if isinstance(figma_id, str) and figma_id:
        return figma_id
    return "frame"


def _starts_with_png(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(len(_PNG)).startswith(_PNG)
    except OSError as error:
        raise ValueError(f"{path}: {error}") from error


def _elements(template: dict):
    for page in template["pages"]:
        for element in page["elements"]:
            yield element


def _write_outputs(template_dir: Path, template: dict, report: list[str]) -> None:
    try:
        template_dir.mkdir(parents=True, exist_ok=True)
        (template_dir / "template.json").write_text(json.dumps(template, indent=2, sort_keys=True) + "\n")
        (template_dir / "report.txt").write_text("\n".join(report) + "\n")
    except OSError as error:
        raise ValueError(f"{template_dir}: {error}") from error


def _url_map(value: object, source: str) -> dict:
    if not isinstance(value, dict):
        raise FigmaError(f"{source}: response must be an object")
    return value


def _url(urls: dict, item: str, source: str) -> str:
    value = urls.get(item)
    if not isinstance(value, str) or not value:
        raise FigmaError(f"{source} {item}: URL must be text")
    return value


def _text(data: dict, key: str, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be text")
    return value


def _mapping(data: object, path: Path) -> dict:
    if not isinstance(data, dict):
        raise ValueError(f"{path}: document must be a mapping")
    return data


def _read_yaml(path: Path):
    try:
        return yaml.safe_load(path.read_text())
    except (yaml.YAMLError, UnicodeDecodeError, OSError) as error:
        raise ValueError(f"{path}: {error}") from error


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as error:
        raise ValueError(f"{path}: {error}") from error
    if not isinstance(data, dict):
        raise ValueError(f"{path}: template must be an object")
    return data
