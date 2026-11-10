"""Validate an IDML package against the seed schema map."""

import json
import subprocess
import tempfile
import zipfile
import zlib
from pathlib import Path

from makeready.idml.package import aid_instruction

_MISSING = object()


def validate(idml: Path, schema_dir: Path, jing: str = "jing") -> list[str]:
    """Return schema errors, each naming its package part. Empty means valid."""
    idml = Path(idml)
    schema_dir = Path(schema_dir)
    mapping, problem = _load_map(schema_dir)
    if problem is not None:
        return [problem]
    try:
        package = zipfile.ZipFile(idml)
    except (zipfile.BadZipFile, OSError) as error:
        return [f"{idml}: {error}"]
    errors: list[str] = []
    with package, tempfile.TemporaryDirectory(prefix="aid-jing-") as folder:
        root = Path(folder)
        names = package.namelist()
        designmap_bytes, skip_designmap = _recognise_designmap(package, names, errors)
        for name in names:
            if name == "designmap.xml" and skip_designmap:
                continue
            if not name.endswith(".xml"):
                continue
            if not _part_name(name):
                errors.append(f"{name}: path must stay inside the package")
                continue
            schema = _schema_for(mapping, name)
            if schema is None:
                continue
            if schema is _MISSING:
                errors.append(f"{name}: no schema in schema-map.json")
                continue
            schema_path = _schema_path(schema_dir, schema)
            if schema_path is None:
                errors.append(f"{name}: {schema} schema must stay inside the schema directory")
                continue
            part = root / name
            try:
                part.parent.mkdir(parents=True, exist_ok=True)
                if name == "designmap.xml":
                    part.write_bytes(designmap_bytes)
                else:
                    part.write_bytes(package.read(name))
            except (OSError, zipfile.BadZipFile, zlib.error, EOFError) as error:
                errors.append(f"{name}: {str(error) or 'member data ends early'}")
                continue
            if not part.resolve().is_relative_to(root.resolve()):
                errors.append(f"{name}: path must stay inside the package")
                continue
            errors.extend(_jing(jing, schema, schema_path, name, part))
    return errors


def _recognise_designmap(
    package: zipfile.ZipFile, names: list[str], errors: list[str]
) -> tuple[bytes | None, bool]:
    """Read designmap.xml once. The bool tells the schema loop to skip it.

    A missing or unreadable part is reported here. The schema loop must not
    read it again, or a read error would be reported twice.
    """
    if "designmap.xml" not in names:
        errors.append("designmap.xml: missing; InDesign will not open the package")
        return None, False
    try:
        data = package.read("designmap.xml")
    except (OSError, zipfile.BadZipFile, zlib.error, EOFError) as error:
        errors.append(f"designmap.xml: {str(error) or 'member data ends early'}")
        return None, True
    if aid_instruction(data) is None:
        errors.append("designmap.xml: no <?aid?> instruction; InDesign will not open the package")
    return data, False


def _load_map(schema_dir: Path) -> tuple[dict | None, str | None]:
    path = schema_dir.parent / "schema-map.json"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return None, f"{path}: {error}"
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        return None, f"{path}: {error}"
    if not isinstance(data, dict):
        return None, f"{path}: schema map must be an object"
    for key, value in data.items():
        if not isinstance(key, str):
            return None, f"{path}: schema map keys must be text"
        if value is not None and not isinstance(value, str):
            return None, f"{path}: {key} must be a schema path or null"
    return data, None


def _schema_for(mapping: dict, name: str):
    if name in mapping:
        return mapping[name]
    prefixes = [key for key in mapping if key.endswith("/") and name.startswith(key)]
    if not prefixes:
        return _MISSING
    return mapping[max(prefixes, key=len)]


def _schema_path(schema_dir: Path, schema: str) -> Path | None:
    if not isinstance(schema, str) or not _part_name(schema):
        return None
    candidate = (schema_dir / schema).resolve()
    try:
        if not candidate.is_relative_to(schema_dir.resolve()):
            return None
    except OSError:
        return None
    return candidate


def _part_name(name: str) -> bool:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        return False
    path = Path(name)
    if path.is_absolute():
        return False
    return all(part not in ("", ".", "..") for part in path.parts)


def _jing(jing: str, schema: str, schema_path: Path, name: str, part: Path) -> list[str]:
    command = [jing]
    if schema.endswith(".rnc"):
        command.append("-c")
    command.extend([str(schema_path), str(part)])
    try:
        result = subprocess.run(command, capture_output=True, timeout=60)
    except FileNotFoundError:
        raise FileNotFoundError("install jing: sudo apt install jing") from None
    except subprocess.TimeoutExpired:
        return [f"{name}: jing timed out"]
    except OSError as error:
        return [f"{name}: jing: {error}"]
    if result.returncode == 0:
        return []
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        lines = [f"jing exited {result.returncode}"]
    return [f"{name}: {line}" for line in lines]
