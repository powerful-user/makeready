import re
from pathlib import Path

import pytest
from fixture_repo import FIXTURES
from PIL import Image

from makeready.colourcheck import check, image_findings, parse_pdfimages, plate_names

LISTING = """page   num  type   width height color comp bpc  enc interp  object ID x-ppi y-ppi size ratio
--------------------------------------------------------------------------------------------
   1     0 image    2700  1800  cmyk    4   8  jpeg   no        12  0   300   300  120K 0.6%
   1     1 image     600   400  icc     3   8  jpeg   no        13  0   150   150  20K 2.8%
   2     2 image     100   100  index   1   8  image  no        14  0    72    72  1K 10%
   2     3 image     100   100  rgb     3   8  image  no        15  0    72    72  1K 10%
"""
INKS = ("C", "M", "Y", "K", "Brand Blue")


def test_parse_pdfimages():
    assert parse_pdfimages(LISTING) == [
        {"page": 1, "color": "cmyk", "comp": 4},
        {"page": 1, "color": "icc", "comp": 3},
        {"page": 2, "color": "index", "comp": 1},
        {"page": 2, "color": "rgb", "comp": 3},
    ]


def test_image_findings():
    found = sorted((f.level, f.code) for f in image_findings(parse_pdfimages(LISTING)))
    assert found == [("error", "rgb-image"), ("error", "rgb-image"), ("warning", "indexed-image")]


def test_plate_names():
    names = ["sep-1(Cyan).tif", "sep-1(Magenta).tif", "sep-1(Yellow).tif", "sep-1(Black).tif", "sep-1.tif",
             "sep-2(Brand Blue).tif", "sep-2(Cyan).tif"]
    assert plate_names(names) == {"C", "M", "Y", "K", "Brand Blue"}


def test_spot_pdf_matches(tmp_path):
    assert check(FIXTURES / "pdf" / "spot.pdf", INKS, tmp_path) == []


def test_plate_mismatch(tmp_path):
    found = check(FIXTURES / "pdf" / "spot.pdf", ("C", "M", "Y", "K"), tmp_path)
    assert [(f.level, f.code) for f in found] == [("error", "plates")]
    assert "Brand Blue" in found[0].message


def test_rgb_image(tmp_path):
    pdf = tmp_path / "rgb.pdf"
    Image.new("RGB", (60, 40), (200, 80, 30)).save(pdf)
    found = check(pdf, ("C", "M", "Y", "K"), tmp_path / "work")
    assert [(f.level, f.code) for f in found] == [("error", "rgb-image")]


def test_multipage_union(tmp_path):
    assert check(FIXTURES / "pdf" / "spot2.pdf", INKS, tmp_path) == []


def test_colour_work_create_oserror(tmp_path):
    work = tmp_path / "work"
    work.write_text("nope")
    with pytest.raises(RuntimeError, match=re.escape(str(work))):
        check(FIXTURES / "pdf" / "spot.pdf", INKS, work)


def test_colour_work_read_oserror(tmp_path, monkeypatch):
    work = tmp_path / "work"
    original = Path.iterdir

    def boom(self):
        if self == work:
            raise OSError(13, "Permission denied", str(work))
        return original(self)

    monkeypatch.setattr(Path, "iterdir", boom)
    with pytest.raises(RuntimeError, match=re.escape(str(work))):
        check(FIXTURES / "pdf" / "spot.pdf", INKS, work)
