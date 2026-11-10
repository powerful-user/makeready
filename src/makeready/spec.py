"""Resolve and validate a document spec against its template."""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_NAME = re.compile(r"^[a-z0-9-]+$")
_STATUS = {"draft", "approved"}
_FITS = {"fill", "fit", "none"}
OVERRIDE_KEYS = frozenset(
    {"x", "y", "width", "height", "size", "tracking", "leading", "swatch", "opacity", "hidden"}
)
_NUMBERS = frozenset({"x", "y", "width", "height", "size", "tracking", "leading"})


class SpecError(Exception):
    """A document spec, template, or the files they name is invalid."""


@dataclass
class Resolved:
    series: str
    doc: str
    template: str
    slots: dict
    overrides: dict[str, dict]
    status: str


def list_docs(repo, series) -> list[str]:
    _check_name(series)
    folder = Path(repo) / "series" / series
    if not folder.is_dir():
        raise SpecError(f"no series {series}")
    names: set[str] = set()
    series_path = folder / "series.yaml"
    if series_path.is_file():
        for variant in _variants(_read_mapping(series_path), series_path):
            names.add(variant["doc"])
    for path in folder.glob("*.yaml"):
        if path.name == "series.yaml":
            continue
        _check_name(path.stem)
        names.add(path.stem)
    return sorted(names)


def resolve(repo, series, doc) -> Resolved:
    _check_name(series)
    _check_name(doc)
    repo = Path(repo)
    series_path = repo / "series" / series / "series.yaml"
    document_path = doc_path(repo, series, doc)
    series_data = _read_mapping(series_path) if series_path.is_file() else None
    document = _read_mapping(document_path) if document_path.is_file() else None
    variants = _variants(series_data, series_path) if series_data else []
    variant = next((item for item in variants if item["doc"] == doc), None)
    if variant is None and document is None:
        raise SpecError(f"no document {series}/{doc}")

    template = _choose_template(document, document_path, variant, series_data, series_path)
    if template is None:
        raise SpecError(f"{series}/{doc}: no template")

    slots: dict = {}
    overrides: dict = _template_overrides(repo, template)
    if series_data is not None:
        default_slots, default_overrides = _defaults(series_data, series_path)
        slots.update(default_slots)
        overrides = _merge_overrides(overrides, default_overrides)
    if variant is not None:
        slots.update(variant["slots"])
        overrides = _merge_overrides(overrides, variant["overrides"])
    if document is not None:
        slots.update(_slots(document, document_path, "slots"))
        overrides = _merge_overrides(overrides, _overrides(document, document_path, "overrides"))
    status = "draft"
    if document is not None and "status" in document:
        status = document["status"]
    return Resolved(series, doc, template, slots, overrides, status)


def load_template(repo, name) -> dict:
    if not _folder_name(name):
        raise SpecError(f"no template {name}")
    path = Path(repo) / "templates" / name / "template.json"
    if not path.is_file():
        raise SpecError(f"no template {name}")
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as error:
        raise SpecError(f"{path}: {error}") from error
    if not isinstance(data, dict):
        raise SpecError(f"{path}: template must be an object")
    if not isinstance(data.get("pages"), list):
        raise SpecError(f"{path}: pages must be a list")
    return data


def hooks_file(repo, template_name: str, template: dict) -> Path | None:
    """The template's hooks module: a .py file named by `hooks`, beside template.json."""
    name = template.get("hooks")
    if name is None:
        return None
    if not _folder_name(name) or not name.endswith(".py"):
        raise SpecError(f"template {template_name}: hooks must be the name of a .py file beside template.json")
    path = Path(repo) / "templates" / template_name / name
    if not path.is_file():
        raise SpecError(f"missing file: templates/{template_name}/{name}")
    return path


def validate(resolved: Resolved, template: dict) -> None:
    elements = _elements(template)
    ids = [element["id"] for element in elements]
    problems: list[str] = []
    seen: set[str] = set()
    for element_id in ids:
        if element_id in seen:
            problems.append(f"duplicate element {element_id}")
        seen.add(element_id)
    slots = {}
    for element in elements:
        slot = element.get("slot")
        if slot is None:
            continue
        if not isinstance(slot, str) or not slot:
            problems.append(f"{element.get('id')}: slot must be text")
            continue
        slots[slot] = element

    for slot in sorted(slots):
        if slot not in resolved.slots:
            problems.append(f"missing slot {slot}")
    for slot in sorted(resolved.slots):
        if slot not in slots:
            problems.append(f"unknown slot {slot}")
    for slot, element in slots.items():
        if slot not in resolved.slots:
            continue
        _slot_problem(slot, element.get("kind"), resolved.slots[slot], problems)

    known = set(ids)
    for element_id in sorted(resolved.overrides):
        values = resolved.overrides[element_id]
        if element_id not in known:
            problems.append(f"unknown element {element_id}")
            continue
        if not isinstance(values, dict):
            problems.append(f"{element_id}: overrides must be a mapping")
            continue
        for key, value in values.items():
            if key not in OVERRIDE_KEYS:
                problems.append(f"{element_id}: unknown override {key}")
                continue
            problem = _override_problem(element_id, key, value)
            if problem:
                problems.append(problem)

    if not isinstance(resolved.status, str) or resolved.status not in _STATUS:
        problems.append("status must be draft or approved")
    if problems:
        raise SpecError("\n".join(problems))


def slot_text(repo, series, value: str) -> str:
    _check_name(series)
    if not isinstance(value, str):
        raise SpecError(f"{series}: slot text must be text")
    if not value.endswith((".md", ".txt")):
        return value
    root = Path(repo) / "series" / series
    path = _inside(root, value, "series folder")
    if not path.is_file():
        raise SpecError(f"missing file: series/{series}/{value}")
    try:
        return path.read_text()
    except (OSError, UnicodeDecodeError) as error:
        raise SpecError(f"{path}: {error}") from error


def set_status(repo, series, doc, status) -> None:
    _check_name(series)
    _check_name(doc)
    if not isinstance(status, str) or status not in _STATUS:
        raise SpecError(f"{status}: status must be draft or approved")
    path = doc_path(repo, series, doc)
    if not path.exists():
        _atomic_write(path, f"status: {status}\n")
        return
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError) as error:
        raise SpecError(f"{path}: {error}") from error
    try:
        original = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise SpecError(f"{path}: {error}") from error
    if not isinstance(original, dict):
        raise SpecError(f"{path}: document must be a mapping")
    candidate = _replace_status(text, status)
    try:
        loaded = yaml.safe_load(candidate)
    except yaml.YAMLError:
        loaded = None
    expected = dict(original)
    expected["status"] = status
    if loaded != expected:
        content = yaml.safe_dump(expected, sort_keys=False)
    else:
        content = candidate
    _atomic_write(path, content)


def doc_path(repo, series, doc) -> Path:
    return Path(repo) / "series" / series / f"{doc}.yaml"


def _check_name(name: str) -> None:
    if not isinstance(name, str) or _NAME.fullmatch(name) is None:
        shown = name if isinstance(name, str) else "name"
        raise SpecError(
            f"{shown}: series and document names are lowercase letters, digits and hyphens"
        )


def _folder_name(name: object) -> bool:
    return isinstance(name, str) and name not in ("", ".", "..") and name == Path(name).name


def _choose_template(document, document_path, variant, series_data, series_path):
    if document is not None and document.get("template") is not None:
        return _template_field(document["template"], document_path)
    if variant is not None and variant.get("template") is not None:
        return variant["template"]
    if series_data is not None and series_data.get("template") is not None:
        return _template_field(series_data["template"], series_path)
    return None


def _template_field(value, path: Path) -> str:
    if not _folder_name(value):
        raise SpecError(f"{path}: template must be a folder name under templates/")
    return value


def _read_mapping(path: Path) -> dict:
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


def _defaults(data: dict, path: Path) -> tuple[dict, dict]:
    if "defaults" not in data or data["defaults"] is None:
        return {}, {}
    defaults = data["defaults"]
    if not isinstance(defaults, dict):
        raise SpecError(f"{path}: defaults must be a mapping")
    return _slots(defaults, path, "defaults.slots"), _overrides(defaults, path, "defaults.overrides")


def _variants(data: dict, path: Path) -> list[dict]:
    if "variants" not in data or data["variants"] is None:
        return []
    variants = data["variants"]
    if not isinstance(variants, list):
        raise SpecError(f"{path}: variants must be a list")
    clean = []
    for index, variant in enumerate(variants):
        if not isinstance(variant, dict):
            raise SpecError(f"{path}: variants[{index}] must be a mapping")
        doc = variant.get("doc")
        if not isinstance(doc, str) or _NAME.fullmatch(doc) is None:
            raise SpecError(
                f"{doc}: series and document names are lowercase letters, digits and hyphens"
            )
        item = {
            "doc": doc,
            "slots": _slots(variant, path, f"variants[{index}].slots"),
            "overrides": _overrides(variant, path, f"variants[{index}].overrides"),
        }
        if variant.get("template") is not None:
            item["template"] = _template_field(variant["template"], path)
        clean.append(item)
    return clean


def _slots(data: dict, path: Path, label: str) -> dict:
    if "slots" not in data or data["slots"] is None:
        return {}
    slots = data["slots"]
    if not isinstance(slots, dict):
        raise SpecError(f"{path}: {label} must be a mapping")
    for key in slots:
        if not isinstance(key, str) or not key:
            raise SpecError(f"{path}: {label} names must be text")
    return dict(slots)


def _overrides(data: dict, path: Path, label: str) -> dict:
    if "overrides" not in data or data["overrides"] is None:
        return {}
    overrides = data["overrides"]
    if not isinstance(overrides, dict):
        raise SpecError(f"{path}: {label} must be a mapping")
    clean = {}
    for key, value in overrides.items():
        if not isinstance(key, str) or not key:
            raise SpecError(f"{path}: {label} element names must be text")
        if not isinstance(value, dict):
            raise SpecError(f"{path}: {label}.{key} must be a mapping")
        for override_key in value:
            if not isinstance(override_key, str):
                raise SpecError(f"{path}: {label}.{key} keys must be text")
        clean[key] = dict(value)
    return clean


def _template_overrides(repo: Path, template: str) -> dict:
    path = repo / "templates" / template / "template-overrides.yaml"
    if not path.is_file():
        return {}
    return _overrides(_read_mapping(path), path, "overrides")


def _merge_overrides(base: dict, extra: dict) -> dict:
    merged = {key: dict(value) for key, value in base.items()}
    for key, value in extra.items():
        merged.setdefault(key, {}).update(value)
    return merged


def _elements(template: dict) -> list[dict]:
    pages = template.get("pages")
    if not isinstance(pages, list):
        raise SpecError("template: pages must be a list")
    elements = []
    for index, page in enumerate(pages):
        if not isinstance(page, dict):
            raise SpecError(f"template: pages[{index}] must be a mapping")
        raw = page.get("elements")
        if not isinstance(raw, list):
            raise SpecError(f"template: pages[{index}].elements must be a list")
        for eindex, element in enumerate(raw):
            where = f"template: pages[{index}].elements[{eindex}]"
            if not isinstance(element, dict):
                raise SpecError(f"{where} must be a mapping")
            element_id = element.get("id")
            if not isinstance(element_id, str) or not element_id:
                raise SpecError(f"{where}.id must be text")
            elements.append(element)
    return elements


def _slot_problem(slot: str, kind: object, value: object, problems: list[str]) -> None:
    if kind == "text":
        if not isinstance(value, str):
            problems.append(f"{slot}: text slot must be text")
        return
    if kind == "image":
        if not isinstance(value, dict):
            problems.append(f"{slot}: image slot must be a mapping")
            return
        src = value.get("src")
        if not isinstance(src, str) or not src:
            problems.append(f"{slot}: src must be text")
        elif not _relative_parts(src):
            problems.append(f"{slot}: src must stay inside the photos folder")
        fit = value.get("fit")
        if "fit" in value and (not isinstance(fit, str) or fit not in _FITS):
            problems.append(f"{slot}: fit must be fill, fit or none")
        if "focus" in value and not _focus(value["focus"]):
            problems.append(f"{slot}: focus must be two numbers from 0 to 1")
        return
    problems.append(f"{slot}: slot must be on a text or image element")


def _override_problem(element_id: str, key: str, value: object) -> str | None:
    if key in _NUMBERS:
        if not _number(value):
            return f"{element_id}: {key} must be a number"
        return None
    if key == "opacity":
        if not _number(value) or not 0 <= float(value) <= 1:
            return f"{element_id}: opacity must be a number from 0 to 1"
        return None
    if key == "swatch":
        if not isinstance(value, str) or not value:
            return f"{element_id}: swatch must be text"
        return None
    if key == "hidden" and not isinstance(value, bool):
        return f"{element_id}: hidden must be true or false"
    return None


def _focus(value: object) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return False
    return all(_number(item) and 0 <= float(item) <= 1 for item in value)


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _relative_parts(value: str) -> bool:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        return False
    path = Path(value)
    if path.is_absolute():
        return False
    return all(part not in ("", ".", "..") for part in path.parts)


def _inside(root: Path, relative: str, where: str) -> Path:
    if not _relative_parts(relative):
        raise SpecError(f"{relative}: path must stay inside the {where}")
    root_resolved = root.resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root_resolved):
        raise SpecError(f"{relative}: path must stay inside the {where}")
    return root / relative


def _replace_status(text: str, status: str) -> str:
    lines = text.splitlines(keepends=True)
    replaced = False
    out = []
    for line in lines:
        if not replaced and line.startswith("status:"):
            ending = "\r\n" if line.endswith("\r\n") else "\n"
            out.append(f"status: {status}{ending}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        if text and not text.endswith(("\n", "\r\n")):
            out.append("\n")
        out.append(f"status: {status}\n")
    return "".join(out)


def _atomic_write(path: Path, text: str) -> None:
    if not path.parent.is_dir():
        raise SpecError(f"{path}: directory not found")
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    try:
        temporary.write_text(text)
        os.replace(temporary, path)
    except OSError as error:
        raise SpecError(f"{path}: {error}") from error
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
