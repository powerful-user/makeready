import re

import pytest

from makeready.colour import Swatch, figma_rgb, hex_of, load_swatches, match

YAML = """
swatches:
  - name: Brand Orange
    hex: "#F26B21"
    cmyk: [0, 70, 95, 0]
    pantone: PANTONE 1585 C
    spot: false
  - name: Brand Blue
    hex: "#0055A5"
    cmyk: [100, 60, 0, 5]
    pantone: PANTONE 2935 C
    spot: true
  - name: Ink
    hex: "#1A1A1A"
    cmyk: [0, 0, 0, 90]
"""


def swatches(tmp_path):
    path = tmp_path / "swatches.yaml"
    path.write_text(YAML)
    return load_swatches(path)


def test_load(tmp_path):
    s = swatches(tmp_path)
    assert s[0] == Swatch(
        name="Brand Orange", hex="#F26B21", cmyk=(0.0, 70.0, 95.0, 0.0), spot=False, pantone="PANTONE 1585 C"
    )
    assert s[1].spot is True
    assert s[2].pantone is None
    assert s[2].spot is False


def test_figma_rgb():
    assert figma_rgb({"r": 0.9490196, "g": 0.4196078, "b": 0.1294118, "a": 1}) == (242, 107, 33)
    assert hex_of((242, 107, 33)) == "#F26B21"


def test_match_exact_and_tolerance(tmp_path):
    s = swatches(tmp_path)
    assert match((242, 107, 33), s).name == "Brand Orange"
    assert match((244, 105, 33), s).name == "Brand Orange"
    assert match((245, 107, 33), s) is None


def test_match_nearest(tmp_path):
    assert match((27, 26, 26), swatches(tmp_path)).name == "Ink"


@pytest.mark.parametrize(
    "bad, message",
    [
        ('swatches:\n  - name: X\n    hex: "F26B21"\n    cmyk: [0, 0, 0, 0]\n', "hex"),
        ('swatches:\n  - name: X\n    hex: "#F26B21"\n    cmyk: [0, 0, 0]\n', "cmyk"),
        ('swatches:\n  - name: X\n    hex: "#F26B21"\n    cmyk: [0, 0, 0, 120]\n', "cmyk"),
        (
            'swatches:\n  - name: X\n    hex: "#F26B21"\n    cmyk: [0, 0, 0, 0]\n'
            '  - name: X\n    hex: "#000000"\n    cmyk: [0, 0, 0, 100]\n',
            "duplicate",
        ),
    ],
)
def test_invalid(tmp_path, bad, message):
    path = tmp_path / "s.yaml"
    path.write_text(bad)
    with pytest.raises(ValueError, match=message):
        load_swatches(path)


@pytest.mark.parametrize("name", ["null", "[]", "3"])
def test_name_must_be_text(tmp_path, name):
    path = tmp_path / "swatches.yaml"
    path.write_text(f'swatches:\n  - name: {name}\n    hex: "#000000"\n    cmyk: [0, 0, 0, 100]\n')
    with pytest.raises(ValueError, match=re.escape(f"{path}: swatch name must be text")):
        load_swatches(path)


def test_malformed_yaml(tmp_path):
    path = tmp_path / "swatches.yaml"
    path.write_text("swatches: [\n")
    with pytest.raises(ValueError, match=re.escape(str(path))):
        load_swatches(path)


def test_duplicate_names_the_file(tmp_path):
    path = tmp_path / "swatches.yaml"
    path.write_text(
        'swatches:\n  - name: X\n    hex: "#F26B21"\n    cmyk: [0, 0, 0, 0]\n'
        '  - name: X\n    hex: "#000000"\n    cmyk: [0, 0, 0, 100]\n'
    )
    with pytest.raises(ValueError, match=re.escape(f"{path}: duplicate swatch name X")):
        load_swatches(path)


@pytest.mark.parametrize(
    "bad, message",
    [
        (
            'swatches:\n  - name: X\n    hex: "F26B21"\n    cmyk: [0, 0, 0, 0]\n',
            "X: hex must be #RRGGBB",
        ),
        (
            'swatches:\n  - name: X\n    hex: "#F26B21"\n    cmyk: [0, 0, 0]\n',
            "X: cmyk must be four numbers from 0 to 100",
        ),
        (
            'swatches:\n  - name: X\n    hex: "#F26B21"\n    cmyk: [0, 0, 0, 0]\n    spot: 1\n',
            "X: spot must be true or false",
        ),
        (
            'swatches:\n  - name: X\n    hex: "#F26B21"\n    cmyk: [0, 0, 0, 0]\n    pantone: 3\n',
            "X: pantone must be a string",
        ),
    ],
)
def test_field_errors_name_the_file(tmp_path, bad, message):
    path = tmp_path / "swatches.yaml"
    path.write_text(bad)
    with pytest.raises(ValueError, match=re.escape(f"{path}: {message}")):
        load_swatches(path)


def test_swatches_not_utf8(tmp_path):
    path = tmp_path / "swatches.yaml"
    path.write_bytes(b"swatches:\n  - name: \xff\n")
    with pytest.raises(ValueError, match=re.escape(str(path))):
        load_swatches(path)
