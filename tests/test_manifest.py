import dataclasses
import hashlib
import json

import pytest
from fixture_repo import make_config, make_repo

from makeready.fontmap import FontEntry, fonts_used, load_font_map
from makeready.manifest import build_id, compute
from makeready.spec import SpecError, load_template, resolve


def manifest(repo, cfg, doc="garden"):
    r = resolve(repo, "spring", doc)
    return compute(repo, cfg, r, load_template(repo, r.template))


def test_font_map(tmp_path):
    repo = make_repo(tmp_path)
    fonts = load_font_map(repo)
    assert fonts["Nimbus Sans/Regular"] == FontEntry("Nimbus Sans", "Regular", "NimbusSans-Regular.otf")
    assert fonts["Nimbus Sans/Bold Italic"] == FontEntry("Nimbus Sans", "Bold Italic", "NimbusSans-BoldItalic.otf")
    assert len(fonts) == 4
    r = resolve(repo, "spring", "garden")
    assert fonts_used(repo, r, load_template(repo, r.template)) == [
        "Nimbus Sans/Bold", "Nimbus Sans/Italic", "Nimbus Sans/Regular",
    ]


def test_keys(tmp_path):
    repo, cfg = make_repo(tmp_path), make_config(tmp_path)
    m = manifest(repo, cfg)
    assert set(m) == {
        "series/spring/garden.yaml", "series/spring/series.yaml", "series/spring/copy/body.md",
        "templates/flyer/template.json", "brand/swatches.yaml", "brand/font-map.yaml",
        "brand/asset-map.yaml", "brand/assets/logo.pdf", "photos/kitchen.jpg",
        "fonts/NimbusSans-Regular.otf", "fonts/NimbusSans-Bold.otf", "fonts/NimbusSans-Italic.otf",
        "seed/reference.idml", "mac/render.jsx", "config:print", "config:mac",
    }
    expected = hashlib.sha256((repo / "series/spring/series.yaml").read_bytes()).hexdigest()
    assert m["series/spring/series.yaml"] == expected


def test_template_overrides_included(tmp_path):
    repo, cfg = make_repo(tmp_path), make_config(tmp_path)
    (repo / "templates/flyer/template-overrides.yaml").write_text("overrides: {}\n")
    assert "templates/flyer/template-overrides.yaml" in manifest(repo, cfg)


def test_variant_without_file(tmp_path):
    repo, cfg = make_repo(tmp_path), make_config(tmp_path)
    assert "series/spring/open-house.yaml" not in manifest(repo, cfg, "open-house")


def test_build_id_stable(tmp_path):
    repo, cfg = make_repo(tmp_path), make_config(tmp_path)
    a, b = build_id(manifest(repo, cfg)), build_id(manifest(repo, cfg))
    assert a == b
    assert len(a) == 16
    canonical = json.dumps(manifest(repo, cfg), sort_keys=True, separators=(",", ":")).encode()
    assert a == hashlib.sha256(canonical).hexdigest()[:16]


@pytest.mark.parametrize(
    "path",
    [
        "series/spring/series.yaml", "series/spring/copy/body.md", "brand/swatches.yaml",
        "brand/assets/logo.pdf", "mac/render.jsx", "seed/reference.idml", "templates/flyer/template.json",
    ],
)
def test_any_input_changes_id(tmp_path, path):
    repo, cfg = make_repo(tmp_path), make_config(tmp_path)
    before = build_id(manifest(repo, cfg))
    with open(repo / path, "ab") as f:
        f.write(b"\n")
    assert build_id(manifest(repo, cfg)) != before


def test_print_config_changes_id(tmp_path):
    repo = make_repo(tmp_path)
    a = build_id(manifest(repo, make_config(tmp_path)))
    b = build_id(manifest(repo, make_config(tmp_path, inks=("C", "M", "Y", "K"))))
    assert a != b


def test_mac_home_changes_id(tmp_path):
    repo, cfg = make_repo(tmp_path), make_config(tmp_path)
    a = build_id(manifest(repo, cfg))
    b = build_id(manifest(repo, dataclasses.replace(cfg, mac_home="/Users/someone-else")))
    assert a != b


def test_missing_photo(tmp_path):
    repo, cfg = make_repo(tmp_path), make_config(tmp_path)
    (repo / "series/spring/garden.yaml").write_text("slots:\n  photo:hero: {src: missing.jpg, fit: fill}\n")
    with pytest.raises(SpecError, match="missing.jpg"):
        manifest(repo, cfg)


def _add_asset(repo, element_id, asset):
    path = repo / "templates/flyer/template.json"
    template = json.loads(path.read_text())
    template["pages"][0]["elements"].append({"id": element_id, "kind": "shape", "asset": asset})
    path.write_text(json.dumps(template))


def test_repo_asset_and_photo_same_key(tmp_path):
    repo, cfg = make_repo(tmp_path), make_config(tmp_path)
    photo = repo / "photos" / "kitchen.jpg"
    photo.parent.mkdir()
    photo.write_bytes(b"not the external photo")
    _add_asset(repo, "repo-photo", "photos/kitchen.jpg")
    with pytest.raises(SpecError) as err:
        manifest(repo, cfg)
    text = str(err.value)
    assert "photos/kitchen.jpg" in text
    assert str(photo) in text
    assert str(cfg.photos / "kitchen.jpg") in text


def test_same_asset_twice_stays_one_entry(tmp_path):
    repo, cfg = make_repo(tmp_path), make_config(tmp_path)
    path = repo / "templates/flyer/template.json"
    template = json.loads(path.read_text())
    logo = next(element for element in template["pages"][0]["elements"] if element["id"] == "Logo")
    second = dict(logo)
    second["id"] = "Logo2"
    template["pages"][0]["elements"].append(second)
    path.write_text(json.dumps(template))
    built = manifest(repo, cfg)
    assert built["brand/assets/logo.pdf"] == hashlib.sha256(
        (repo / "brand/assets/logo.pdf").read_bytes()
    ).hexdigest()


def test_repo_asset_and_font_same_key(tmp_path):
    repo, cfg = make_repo(tmp_path), make_config(tmp_path)
    font = repo / "fonts" / "NimbusSans-Regular.otf"
    font.parent.mkdir()
    font.write_bytes(b"not the mapped font")
    _add_asset(repo, "repo-font", "fonts/NimbusSans-Regular.otf")
    with pytest.raises(SpecError) as err:
        manifest(repo, cfg)
    text = str(err.value)
    assert "fonts/NimbusSans-Regular.otf" in text
    assert str(font) in text
    assert str(cfg.fonts / "NimbusSans-Regular.otf") in text


@pytest.mark.parametrize("key", ["config:print", "config:mac"])
def test_repo_asset_with_config_key(tmp_path, key):
    repo, cfg = make_repo(tmp_path), make_config(tmp_path)
    (repo / key).write_bytes(b"an asset named like a config key")
    _add_asset(repo, "config-asset", key)
    with pytest.raises(SpecError, match=key):
        manifest(repo, cfg)
