"""Check a rendered PDF for RGB images and the plates the printer expects."""

import subprocess
from pathlib import Path

from makeready.checks import Finding

_PROCESS = {"Cyan": "C", "Magenta": "M", "Yellow": "Y", "Black": "K"}


def parse_pdfimages(text: str) -> list[dict]:
    """Rows of `pdfimages -list`: page, colour space and component count."""
    if not isinstance(text, str):
        raise RuntimeError("pdfimages listing must be text")
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped and set(stripped) <= {"-"}:
            start = index + 1
            break
    if start is None:
        raise RuntimeError("pdfimages listing has no header")
    rows = []
    for line in lines[start:]:
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) < 7:
            raise RuntimeError(f"pdfimages listing: {line.strip()} has no colour columns")
        try:
            page = int(parts[0])
            comp = int(parts[6])
        except ValueError as error:
            raise RuntimeError(f"pdfimages listing: {line.strip()} is not a data row") from error
        rows.append({"page": page, "color": parts[5], "comp": comp})
    return rows


def image_findings(rows) -> list[Finding]:
    """An image with 3 components is an error; an indexed image is a warning."""
    if not isinstance(rows, list):
        raise RuntimeError("image rows must be a list")
    findings = []
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("image row must be an object")
        page = row.get("page")
        color = row.get("color")
        comp = row.get("comp")
        if isinstance(page, bool) or not isinstance(page, int):
            raise RuntimeError("image row page must be an integer")
        if not isinstance(color, str):
            raise RuntimeError("image row color must be text")
        if isinstance(comp, bool) or not isinstance(comp, int):
            raise RuntimeError("image row comp must be an integer")
        if comp == 3:
            findings.append(
                Finding(
                    "error",
                    "rgb-image",
                    None,
                    f"page {page}: image with 3 colour components ({color})",
                )
            )
        if color == "index":
            findings.append(Finding("warning", "indexed-image", None, f"page {page}: indexed image"))
    return findings


def plate_names(filenames: list[str]) -> set[str]:
    """Separation names from tiffsep filenames, unioned across pages."""
    if not isinstance(filenames, list) or any(not isinstance(name, str) for name in filenames):
        raise RuntimeError("plate filenames must be a list of names")
    names: set[str] = set()
    for filename in filenames:
        stem = filename[:-4] if filename.endswith(".tif") else ""
        if not stem:
            continue
        open_at = stem.rfind("(")
        close_at = stem.rfind(")")
        if open_at == -1 or close_at <= open_at:
            continue
        plate = stem[open_at + 1 : close_at]
        if plate:
            names.add(_PROCESS.get(plate, plate))
    return names


def check(pdf: Path, inks: tuple[str, ...], work: Path) -> list[Finding]:
    """Raster images and separation plates. Says nothing about vectors."""
    pdf = Path(pdf)
    work = Path(work)
    if isinstance(inks, str) or not isinstance(inks, tuple) or any(not isinstance(ink, str) for ink in inks):
        raise RuntimeError("inks must be a tuple of names")
    findings = image_findings(parse_pdfimages(_run(["pdfimages", "-list", str(pdf)])))
    try:
        work.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise RuntimeError(f"{error.filename or work}: {error}") from error
    _run(
        [
            "gs",
            "-dSAFER",
            "-dBATCH",
            "-dNOPAUSE",
            "-sDEVICE=tiffsep",
            "-dPrintSpotCMYK=true",
            "-o",
            str(work / "sep-%d.tif"),
            str(pdf),
        ]
    )
    try:
        names = [path.name for path in work.iterdir() if path.name.endswith(".tif")]
    except OSError as error:
        raise RuntimeError(f"{error.filename or work}: {error}") from error
    found = plate_names(names)
    expected = set(inks)
    if found != expected:
        missing = sorted(expected - found)
        unexpected = sorted(found - expected)
        parts = []
        if missing:
            parts.append("missing plates: " + ", ".join(missing))
        if unexpected:
            parts.append("unexpected plates: " + ", ".join(unexpected))
        findings.append(Finding("error", "plates", None, "; ".join(parts)))
    return findings


def _run(argv: list[str]) -> str:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, errors="replace")
    except OSError as error:
        raise RuntimeError(f"{argv[0]}: {error}") from error
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit {result.returncode}"
        raise RuntimeError(f"{argv[0]} failed: {detail}")
    return result.stdout
