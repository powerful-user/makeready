"""Compile a document into an immutable build directory."""

import json
import os
import re
import shutil
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

from makeready.checks import Finding, run
from makeready.idml.document import compile_document
from makeready.idml.package import write_package
from makeready.idml.validate import validate as validate_idml
from makeready.manifest import build_id, compute
from makeready.spec import SpecError, load_template, resolve, validate as validate_spec

_BUILD_ID = re.compile(r"^[0-9a-f]{16}$")
_JING_MISSING = "install jing: sudo apt install jing"


class BuildFailed(Exception):
    """Checks, schema validation or I/O failed. This attempt leaves no build directory."""

    def __init__(self, findings: list[Finding] | None = None, errors: list[str] | None = None):
        self.findings = list(findings or [])
        self.errors = list(errors or [])
        lines = [finding.message for finding in self.findings if finding.level == "error"]
        lines.extend(self.errors)
        super().__init__("\n".join(lines) or "build failed")


@dataclass(frozen=True)
class BuildResult:
    build_id: str
    path: Path
    findings: list[Finding]


def build(repo, cfg, series, doc, reopen: bool = False) -> BuildResult:
    """Compile `series/doc`, or reuse the build its inputs already produced."""
    repo = Path(repo)
    resolved = resolve(repo, series, doc)
    template = load_template(repo, resolved.template)
    validate_spec(resolved, template)
    if resolved.status == "approved" and not reopen:
        raise SpecError(
            f"{series}/{doc} is approved; makeready build --reopen discards hand edits to its packaged .indd"
        )
    findings = run(repo, cfg, resolved, template)
    if any(finding.level == "error" for finding in findings):
        raise BuildFailed(findings)

    manifest = compute(repo, cfg, resolved, template)
    identifier = build_id(manifest)
    if _BUILD_ID.fullmatch(identifier) is None:
        raise SpecError(f"{identifier}: build id must be 16 hex characters")
    doc_dir = repo / "build" / series / doc
    dest = doc_dir / identifier
    idml_name = f"{doc}.idml"
    created = False
    try:
        try:
            if not (dest / idml_name).is_file():
                if dest.exists():
                    raise BuildFailed(findings, [f"{dest}: build directory exists without {idml_name}"])
                created = _write_new(repo, cfg, resolved, template, findings, manifest, dest, idml_name)
            _point_latest(doc_dir, identifier, findings)
        except OSError as error:
            where = getattr(error, "filename", None) or dest
            _io_failure(findings, Path(where), error)
    except Exception:
        try:
            if created and dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
                _prune(doc_dir)
        except OSError:
            pass
        raise
    return BuildResult(identifier, dest, findings)


def _write_new(repo, cfg, resolved, template, findings, manifest, dest: Path, idml_name: str) -> bool:
    """Write a new build. Return True when this attempt published `dest`."""
    temp = dest.parent / f".{dest.name}.tmp-{os.getpid()}"
    if not _inside(repo / "build", temp):
        raise SpecError(f"{temp}: path must stay inside build/")
    published = False
    owned = False
    parent_existed = True
    try:
        try:
            if temp.exists() or temp.is_symlink():
                raise BuildFailed(findings, [f"{temp}: temporary build directory already exists"])
            parent_existed = temp.parent.exists()
            _mkdir(temp.parent, findings, parents=True)
            _mkdir(temp, findings)
            owned = True
            try:
                compiled = compile_document(repo, cfg, resolved, template)
            except OSError as error:
                where = getattr(error, "filename", None) or temp
                _io_failure(findings, Path(where), error)
            seed = repo / "seed" / "reference.idml"
            try:
                write_package(seed, temp / idml_name, compiled.parts)
            except (OSError, zipfile.BadZipFile, zlib.error, KeyError) as error:
                text = str(error)
                message = text if str(seed) in text else f"{seed}: {text}"
                raise BuildFailed(findings, [message]) from error
            _copy_files(temp / "links", _link_pairs(compiled.links), "links", findings)
            _copy_files(temp / "Document Fonts", _font_pairs(compiled.fonts), "Document Fonts", findings)
            _put_text(temp / "manifest.json", _canonical(manifest), findings)
            _put_text(temp / "checks.json", _pretty(_warnings(findings)), findings)
            _put_text(temp / "geometry.json", _pretty(compiled.geometry), findings)
            schema_errors = _schema_errors(temp / idml_name, repo / "seed" / "schema")
            if schema_errors:
                raise BuildFailed(findings, schema_errors)
            try:
                os.replace(temp, dest)
            except OSError as error:
                if not _has_idml(dest, idml_name, findings):
                    _io_failure(findings, dest, error)
            else:
                published = True
        except OSError as error:
            where = getattr(error, "filename", None) or temp
            _io_failure(findings, Path(where), error)
    finally:
        if owned and not published:
            _discard(temp)
        elif not parent_existed and not owned:
            _prune(temp.parent)
    return published


def _io_failure(findings, path: Path, error: OSError) -> None:
    if str(error) == _JING_MISSING:
        raise BuildFailed(findings, [_JING_MISSING]) from error
    raise BuildFailed(findings, [f"{path}: {error}"]) from error


def _has_idml(dest: Path, idml_name: str, findings) -> bool:
    path = dest / idml_name
    try:
        return path.is_file()
    except OSError as error:
        _io_failure(findings, path, error)


def _mkdir(path: Path, findings, parents: bool = False) -> None:
    try:
        path.mkdir(parents=parents, exist_ok=parents)
    except OSError as error:
        _io_failure(findings, path, error)


def _put_text(path: Path, text: str, findings) -> None:
    try:
        _write_text(path, text)
    except OSError as error:
        _io_failure(findings, path, error)


def _schema_errors(idml: Path, schema_dir: Path) -> list[str]:
    try:
        return validate_idml(idml, schema_dir)
    except FileNotFoundError:
        raise
    except OSError as error:
        return [str(error)]


def _link_pairs(links) -> list[tuple[str, Path]]:
    if not isinstance(links, dict):
        raise SpecError("links must be a mapping")
    pairs = []
    for name, source in links.items():
        if not isinstance(name, str):
            raise SpecError("link name must be text")
        pairs.append((name, Path(source)))
    return pairs


def _font_pairs(fonts) -> list[tuple[str, Path]]:
    if not isinstance(fonts, list):
        raise SpecError("fonts must be a list")
    pairs = []
    for source in fonts:
        path = Path(source)
        pairs.append((path.name, path))
    return pairs


def _copy_files(folder: Path, pairs: list[tuple[str, Path]], label: str, findings) -> None:
    _mkdir(folder, findings)
    seen: dict[str, Path] = {}
    for name, source in pairs:
        safe = _segment(name, label)
        try:
            resolved = source.resolve()
        except OSError as error:
            raise BuildFailed(findings, [f"{label}/{safe}: {error}"]) from error
        previous = seen.get(safe)
        if previous is not None:
            if previous == resolved:
                continue
            raise SpecError(f"{label}/{safe}: two files would use the same name")
        try:
            present = resolved.is_file()
        except OSError as error:
            raise BuildFailed(findings, [f"{source}: {error}"]) from error
        if not present:
            raise BuildFailed(findings, [f"missing file: {source}"])
        target = folder / safe
        if not _inside(folder, target):
            raise SpecError(f"{label}/{safe}: path must stay inside {label}")
        seen[safe] = resolved
        _copy_file(resolved, target, findings)


def _copy_file(source: Path, dest: Path, findings) -> None:
    temporary = dest.with_name(dest.name + f".tmp-{os.getpid()}")
    owned = False
    try:
        if temporary.exists() or temporary.is_symlink():
            raise BuildFailed(findings, [f"{temporary}: temporary file already exists"])
        owned = True
        shutil.copyfile(source, temporary)
        os.replace(temporary, dest)
        owned = False
    except OSError as error:
        raise BuildFailed(findings, [f"{source}: {error}"]) from error
    finally:
        if owned:
            _unlink_quiet(temporary)


def _warnings(findings: list[Finding]) -> list[dict]:
    return [
        {"level": finding.level, "code": finding.code, "element": finding.element, "message": finding.message}
        for finding in findings
        if finding.level == "warning"
    ]


def _canonical(payload) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _pretty(payload) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _write_text(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    owned = False
    try:
        if temporary.exists() or temporary.is_symlink():
            raise FileExistsError(f"{temporary}: temporary file already exists")
        owned = True
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
        owned = False
    finally:
        if owned:
            _unlink_quiet(temporary)


def _point_latest(folder: Path, identifier: str, findings) -> None:
    temporary = folder / f".latest.tmp-{os.getpid()}"
    owned = False
    try:
        if temporary.exists() or temporary.is_symlink():
            raise BuildFailed(findings, [f"{temporary}: temporary link already exists"])
        os.symlink(identifier, temporary)
        owned = True
        os.replace(temporary, folder / "latest")
        owned = False
    except OSError as error:
        raise BuildFailed(findings, [f"{folder / 'latest'}: {error}"]) from error
    finally:
        if owned:
            try:
                if temporary.is_symlink() or temporary.exists():
                    temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _unlink_quiet(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _discard(temp: Path) -> None:
    try:
        if temp.is_symlink() or (temp.exists() and not temp.is_dir()):
            temp.unlink(missing_ok=True)
        elif temp.is_dir():
            shutil.rmtree(temp)
    except OSError:
        return
    _prune(temp.parent)


def _prune(folder: Path) -> None:
    for _ in range(3):
        try:
            folder.rmdir()
        except OSError:
            return
        folder = folder.parent


def _segment(name: str, label: str) -> str:
    if (
        not isinstance(name, str)
        or name in ("", ".", "..")
        or "\\" in name
        or "/" in name
        or "\x00" in name
        or name != Path(name).name
    ):
        raise SpecError(f"{label}/{name}: file name must stay inside {label}")
    return name


def _inside(root: Path, path: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False
