import re

import pytest
from PIL import Image

from makeready.proof import make_proof

GEOMETRY = {
    "pages": [
        {"name": "Front", "width": 100, "height": 50, "bleed": 10, "elements": [{"id": "box", "bbox": [10, 10, 40, 30]}]}
    ]
}


def page_pdf(path, w, h):
    Image.new("RGB", (w, h), "white").save(path, resolution=72)
    return path


def test_proof_draws_boxes(tmp_path):
    out = make_proof(page_pdf(tmp_path / "doc.pdf", 120, 70), GEOMETRY, tmp_path / "proof", dpi=72)
    assert [p.name for p in out] == ["proof-1.png"]
    img = Image.open(out[0]).convert("RGB")
    assert img.size == (120, 70)
    red = img.getpixel((20, 25))
    assert red[0] > 200
    assert red[1] < 80
    assert img.getpixel((70, 60)) == (255, 255, 255)


def test_proof_create_oserror(tmp_path):
    out = tmp_path / "proof"
    out.write_text("nope")
    with pytest.raises(RuntimeError, match=re.escape(str(out))):
        make_proof(page_pdf(tmp_path / "doc.pdf", 120, 70), GEOMETRY, out, dpi=72)


def test_proof_read_oserror(tmp_path, monkeypatch):
    def boom(path, *args, **kwargs):
        raise OSError(13, "Permission denied", str(path))

    monkeypatch.setattr("makeready.proof.Image.open", boom)
    with pytest.raises(RuntimeError, match="Permission denied"):
        make_proof(page_pdf(tmp_path / "doc.pdf", 120, 70), GEOMETRY, tmp_path / "proof", dpi=72)


def test_proof_write_oserror(tmp_path, monkeypatch):
    def boom(src, dst):
        raise OSError(13, "Permission denied", str(dst))

    monkeypatch.setattr("makeready.proof.os.replace", boom)
    with pytest.raises(RuntimeError, match="proof-1.png"):
        make_proof(page_pdf(tmp_path / "doc.pdf", 120, 70), GEOMETRY, tmp_path / "proof", dpi=72)


def test_reference_overlay(tmp_path):
    ref = tmp_path / "ref"
    ref.mkdir()
    Image.new("RGB", (200, 100), (0, 0, 255)).save(ref / "Front.png")
    out = make_proof(page_pdf(tmp_path / "doc.pdf", 120, 70), GEOMETRY, tmp_path / "proof", reference_dir=ref, dpi=72)
    img = Image.open(out[0]).convert("RGB")
    inside = img.getpixel((60, 45))
    assert inside[2] > inside[0]
    assert inside[0] < 255
    assert img.getpixel((5, 5)) == (255, 255, 255)
