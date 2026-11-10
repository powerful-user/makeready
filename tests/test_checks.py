import dataclasses
import json
from pathlib import Path

import pytest
from fixture_repo import make_config, make_repo
from PIL import Image, ImageCms

from makeready.checks import run
from makeready.spec import SpecError, load_template, resolve


def findings(repo, cfg, doc="garden"):
    r = resolve(repo, "spring", doc)
    return run(repo, cfg, r, load_template(repo, r.template))


def codes(fs):
    return sorted((f.level, f.code, f.element) for f in fs)


def test_clean_fixture(tmp_path):
    assert findings(make_repo(tmp_path), make_config(tmp_path)) == []


def test_missing_files(tmp_path):
    repo, cfg = make_repo(tmp_path), make_config(tmp_path)
    (repo / "series/spring/copy/body.md").unlink()
    (repo / "brand/assets/logo.pdf").unlink()
    assert codes(findings(repo, cfg)) == [("error", "missing-file", "Logo"), ("error", "missing-file", "body")]


def test_low_resolution(tmp_path):
    repo = make_repo(tmp_path)
    photos = tmp_path / "photos"
    photos.mkdir()
    Image.new("RGB", (600, 400)).save(photos / "kitchen.jpg", dpi=(72, 72))
    fs = findings(repo, dataclasses.replace(make_config(tmp_path), photos=photos))
    assert codes(fs) == [("warning", "low-resolution", "photo:hero")]
    assert "ppi" in fs[0].message


def test_unknown_swatch(tmp_path):
    repo, cfg = make_repo(tmp_path), make_config(tmp_path)
    (repo / "series/spring/garden.yaml").write_text("overrides:\n  Band: {swatch: Neon}\n")
    assert codes(findings(repo, cfg)) == [("error", "unknown-swatch", "Band")]


def test_ink_mismatch(tmp_path):
    fs = findings(make_repo(tmp_path), make_config(tmp_path, inks=("C", "M", "Y", "K")))
    assert codes(fs) == [("error", "ink-mismatch", None)]
    assert "Brand Blue" in fs[0].message


def test_bleed_short(tmp_path):
    repo, cfg = make_repo(tmp_path), make_config(tmp_path)
    (repo / "series/spring/garden.yaml").write_text("overrides:\n  photo:hero: {x: 5}\n")
    fs = findings(repo, cfg)
    assert codes(fs) == [("warning", "bleed", "photo:hero")]
    assert "left" in fs[0].message


def test_bleed_skips_text(tmp_path):
    repo, cfg = make_repo(tmp_path), make_config(tmp_path)
    (repo / "series/spring/garden.yaml").write_text("overrides:\n  headline: {x: -35}\n")
    assert findings(repo, cfg) == []


def test_missing_font(tmp_path):
    cfg = dataclasses.replace(make_config(tmp_path), fonts=tmp_path)
    assert codes(findings(make_repo(tmp_path), cfg)) == [
        ("error", "missing-font", "Nimbus Sans/Bold"),
        ("error", "missing-font", "Nimbus Sans/Italic"),
        ("error", "missing-font", "Nimbus Sans/Regular"),
    ]


def test_unmapped_font(tmp_path):
    repo, cfg = make_repo(tmp_path), make_config(tmp_path)
    (repo / "brand/font-map.yaml").write_text("fonts: {}\n")
    assert codes(findings(repo, cfg)) == [
        ("error", "font-unmapped", "Nimbus Sans/Bold"),
        ("error", "font-unmapped", "Nimbus Sans/Italic"),
        ("error", "font-unmapped", "Nimbus Sans/Regular"),
    ]


def test_cmyk_profile(tmp_path):
    photos = tmp_path / "photos"
    photos.mkdir()
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    Image.new("CMYK", (2700, 1800)).save(photos / "kitchen.jpg", dpi=(300, 300), icc_profile=srgb)
    cfg = dataclasses.replace(make_config(tmp_path), photos=photos)
    assert codes(findings(make_repo(tmp_path), cfg)) == [("warning", "cmyk-profile", "photo:hero")]


def test_unreadable_swatches_are_a_spec_error(tmp_path, monkeypatch):
    repo, cfg = make_repo(tmp_path), make_config(tmp_path)
    real = Path.read_text

    def read_text(self, *args, **kwargs):
        if self.name == "swatches.yaml":
            raise OSError("permission denied")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    with pytest.raises(SpecError, match="brand/swatches.yaml"):
        findings(repo, cfg)


def test_kind_must_be_text(tmp_path):
    repo, cfg = make_repo(tmp_path), make_config(tmp_path)
    path = repo / "templates/flyer/template.json"
    template = json.loads(path.read_text())
    template["pages"][0]["elements"][0]["kind"] = []
    path.write_text(json.dumps(template))
    with pytest.raises(SpecError, match="headline: kind must be text, image, shape or vector"):
        findings(repo, cfg)


def test_unsupported_asset(tmp_path):
    repo, cfg = make_repo(tmp_path), make_config(tmp_path)
    path = repo / "templates/flyer/template.json"
    t = json.loads(path.read_text())
    t["pages"][0]["elements"][4]["asset"] = "brand/assets/logo.eps"
    path.write_text(json.dumps(t))
    (repo / "brand/assets/logo.eps").write_text("%!PS-Adobe-3.0 EPSF-3.0\n")
    assert codes(findings(repo, cfg)) == [("error", "unsupported-asset", "Logo")]


def _band_near_edge(repo, nonprinting):
    path = repo / "templates/flyer/template.json"
    template = json.loads(path.read_text())
    for page in template["pages"]:
        for element in page["elements"]:
            if element["id"] == "Band":
                element["transform"] = [1, 0, 0, 1, 0, 100]
                if nonprinting:
                    element["nonprinting"] = True
    path.write_text(json.dumps(template))


def test_bleed_warns_for_a_printing_shape(tmp_path):
    repo, cfg = make_repo(tmp_path), make_config(tmp_path)
    _band_near_edge(repo, nonprinting=False)
    assert codes(findings(repo, cfg)) == [("warning", "bleed", "Band")]


def test_bleed_skips_nonprinting_shapes(tmp_path):
    repo, cfg = make_repo(tmp_path), make_config(tmp_path)
    _band_near_edge(repo, nonprinting=True)
    assert findings(repo, cfg) == []
