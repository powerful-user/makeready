"""Write an IDML zip from a seed package and replacement parts."""

import os
import re
import zipfile
import zlib
from pathlib import Path

# Prolog only: optional XML declaration, whitespace, then <?aid … ?> at the first byte.
_AID_INSTRUCTION = re.compile(
    rb"\A(?:<\?xml[ \t\r\n].*?\?>)?[ \t\r\n]*(<\?aid[ \t\r\n].*?\?>)",
    re.DOTALL,
)


def aid_instruction(data: bytes) -> bytes | None:
    """Return the `<?aid … ?>` instruction from a part's prolog, or None."""
    found = _AID_INSTRUCTION.match(data)
    if found is None:
        return None
    return found.group(1)


def write_package(
    seed: Path,
    out: Path,
    parts: dict[str, bytes],
    drop_prefixes: tuple[str, ...] = ("Spreads/", "Stories/"),
) -> None:
    """Write `out`, replacing seed entries and dropping whole folders.

    `mimetype` is first and stored uncompressed, using the seed's bytes.
    The package is written to a temporary path and renamed, so a failure
    leaves no half-written file.
    """
    seed = Path(seed)
    out = Path(out)
    temporary = out.with_name(out.name + ".tmp")
    owned = False
    try:
        if temporary.exists() or temporary.is_symlink():
            raise FileExistsError(f"{temporary}: temporary file already exists")
        owned = True
        with zipfile.ZipFile(seed) as source, zipfile.ZipFile(temporary, "w") as package:
            _store(package, "mimetype", _read_member(source, seed, "mimetype"), zipfile.ZIP_STORED)
            for info in source.infolist():
                name = info.filename
                if name == "mimetype" or name in parts or _dropped(name, drop_prefixes):
                    continue
                _store(package, name, _read_member(source, seed, name), zipfile.ZIP_DEFLATED)
            for name in sorted(parts):
                _store(package, name, parts[name], zipfile.ZIP_DEFLATED)
        os.replace(temporary, out)
        owned = False
    finally:
        if owned:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def read_part(path: Path, name: str) -> bytes:
    """Return one package part, or raise KeyError when it is absent."""
    path = Path(path)
    with zipfile.ZipFile(path) as package:
        try:
            return _read_member(package, path, name)
        except KeyError:
            raise KeyError(name) from None


def _read_member(package: zipfile.ZipFile, path: Path, name: str) -> bytes:
    try:
        return package.read(name)
    except (zipfile.BadZipFile, zlib.error, EOFError) as error:
        raise zipfile.BadZipFile(f"{path}: {name}: {str(error) or 'member data ends early'}") from error


def _store(package: zipfile.ZipFile, name: str, data: bytes, compression: int) -> None:
    info = zipfile.ZipInfo(name)
    info.compress_type = compression
    package.writestr(info, data)


def _dropped(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name.startswith(prefix) for prefix in prefixes)
