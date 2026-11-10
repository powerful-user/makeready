"""Raster proofs with element boxes and an optional Figma reference overlay."""

import os
import re
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

_PAGE = re.compile(r"(\d+)$")


def make_proof(pdf: Path, geometry: dict, out_dir: Path, reference_dir: Path | None = None, dpi: int = 150) -> list[Path]:
    """Write proof-<n>.png for each page and return the paths in page order."""
    pdf = Path(pdf)
    out_dir = Path(out_dir)
    if isinstance(dpi, bool) or not isinstance(dpi, int) or dpi <= 0:
        raise RuntimeError("dpi must be a positive integer")
    pages = _pages(geometry)
    try:
        return _write_proofs(pdf, pages, out_dir, reference_dir, dpi)
    except OSError as error:
        where = error.filename or out_dir
        raise RuntimeError(f"{where}: {error}") from error


def _write_proofs(pdf: Path, pages: list, out_dir: Path, reference_dir: Path | None, dpi: int) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    raster = out_dir / f".pages.tmp-{os.getpid()}"
    if raster.exists() or raster.is_symlink():
        raise RuntimeError(f"{raster}: temporary directory already exists")
    owned = False
    temps: list[Path] = []
    try:
        raster.mkdir()
        owned = True
        _run(["pdftoppm", "-r", str(dpi), "-png", str(pdf), str(raster / "page")])
        images = _page_images(raster)
        if not images:
            raise RuntimeError(f"pdftoppm wrote no pages for {pdf}")
        pending: list[tuple[Path, Path]] = []
        for index, image_path in enumerate(images):
            page = pages[index] if index < len(pages) else None
            dest = out_dir / f"proof-{index + 1}.png"
            temp = out_dir / f".proof-{index + 1}.tmp-{os.getpid()}"
            if temp.exists() or temp.is_symlink():
                raise RuntimeError(f"{temp}: temporary file already exists")
            _compose(image_path, page, reference_dir, dpi).save(temp, format="PNG")
            temps.append(temp)
            pending.append((temp, dest))
        published = []
        for temp, dest in pending:
            os.replace(temp, dest)
            temps.remove(temp)
            published.append(dest)
        return published
    finally:
        for temp in temps:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
        if owned:
            shutil.rmtree(raster, ignore_errors=True)


def _compose(image_path: Path, page: dict | None, reference_dir: Path | None, dpi: int) -> Image.Image:
    image = Image.open(image_path).convert("RGB")
    if page is None:
        return image
    px = dpi / 72
    bleed = _number(page.get("bleed"), "bleed")
    width = _number(page.get("width"), "width")
    height = _number(page.get("height"), "height")
    name = page.get("name")
    if not isinstance(name, str):
        raise RuntimeError("page name must be text")
    origin_x = round(bleed * px)
    origin_y = round(bleed * px)
    trim_w = max(round(width * px), 1)
    trim_h = max(round(height * px), 1)
    reference = _reference(reference_dir, name, trim_w, trim_h)
    if reference is not None and origin_x >= 0 and origin_y >= 0:
        box = (origin_x, origin_y, origin_x + trim_w, origin_y + trim_h)
        if box[2] <= image.width and box[3] <= image.height:
            base = image.crop(box)
            image.paste(Image.blend(base, reference, 0.35), (origin_x, origin_y))
    draw = ImageDraw.Draw(image)
    elements = page.get("elements")
    if not isinstance(elements, list):
        raise RuntimeError(f"{name}: elements must be a list")
    for element in elements:
        if not isinstance(element, dict):
            raise RuntimeError(f"{name}: element must be an object")
        element_id = element.get("id")
        if not isinstance(element_id, str):
            raise RuntimeError(f"{name}: element id must be text")
        box = element.get("bbox")
        if not isinstance(box, list) or len(box) != 4:
            raise RuntimeError(f"{element_id}: bbox must be four numbers")
        x0 = round((_number(box[0], "bbox") + bleed) * px)
        y0 = round((_number(box[1], "bbox") + bleed) * px)
        x1 = round((_number(box[2], "bbox") + bleed) * px)
        y1 = round((_number(box[3], "bbox") + bleed) * px)
        draw.rectangle([x0, y0, x1, y1], outline=(255, 0, 0), width=2)
        draw.text((x0 + 2, y0 + 2), element_id, fill=(255, 0, 0))
    return image


def _reference(reference_dir: Path | None, name: str, trim_w: int, trim_h: int) -> Image.Image | None:
    if reference_dir is None:
        return None
    reference_dir = Path(reference_dir)
    if name != Path(name).name or name in ("", ".", ".."):
        raise RuntimeError(f"{name}: page name must be a file name")
    path = reference_dir / f"{name}.png"
    if not path.is_file():
        return None
    try:
        if not path.resolve().is_relative_to(reference_dir.resolve()):
            raise RuntimeError(f"{path}: path must stay inside {reference_dir}")
        image = Image.open(path).convert("RGB")
    except OSError as error:
        raise RuntimeError(f"{path}: {error}") from error
    return image.resize((trim_w, trim_h), Image.Resampling.LANCZOS)


def _pages(geometry: dict) -> list:
    if not isinstance(geometry, dict):
        raise RuntimeError("geometry must be an object")
    pages = geometry.get("pages")
    if not isinstance(pages, list):
        raise RuntimeError("geometry pages must be a list")
    for page in pages:
        if not isinstance(page, dict):
            raise RuntimeError("geometry page must be an object")
    return pages


def _page_images(folder: Path) -> list[Path]:
    numbered = []
    for path in folder.glob("page*.png"):
        match = _PAGE.search(path.stem)
        number = int(match.group(1)) if match else 0
        numbered.append((number, path.name, path))
    numbered.sort()
    return [path for _, _, path in numbered]


def _number(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"{what} must be a number")
    return float(value)


def _run(argv: list[str]) -> None:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, errors="replace")
    except OSError as error:
        raise RuntimeError(f"{argv[0]}: {error}") from error
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit {result.returncode}"
        raise RuntimeError(f"{argv[0]} failed: {detail}")
