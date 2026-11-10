import xml.etree.ElementTree as ET
from pathlib import Path

from makeready.colour import Swatch
from makeready.idml.package import read_part
from makeready.idml.resources import ParaStyle, fmt, fonts_xml, graphic_xml, styles_xml

SEED = Path(__file__).resolve().parents[1] / "seed" / "reference.idml"
ORANGE = Swatch("Brand Orange", "#F26B21", (0, 70, 95, 0), False, "PANTONE 1585 C")
BLUE = Swatch("Brand Blue", "#0055A5", (100, 60, 0, 5), True, "PANTONE 2935 C")


def colour_names(xml):
    return [c.get("Name") for c in ET.fromstring(xml).iter("Color")]


def colour(xml, name):
    return next(c for c in ET.fromstring(xml).iter("Color") if c.get("Name") == name)


def para(xml, name):
    return next(s for s in ET.fromstring(xml).iter("ParagraphStyle") if s.get("Name") == name)


def test_fmt():
    assert fmt(48.0) == "48"
    assert fmt(57.6) == "57.6"
    assert fmt(-20.000000000000004) == "-20"
    assert fmt(-0.00001) == "0"
    assert fmt(9.948) == "9.948"


def test_graphic_adds_swatches():
    out = graphic_xml(read_part(SEED, "Resources/Graphic.xml"), [ORANGE, BLUE])
    orange, blue = colour(out, "Brand Orange"), colour(out, "Brand Blue")
    assert orange.get("Self") == "Color/Brand Orange"
    assert orange.get("Model") == "Process"
    assert orange.get("Space") == "CMYK"
    assert orange.get("ColorValue") == "0 70 95 0"
    assert blue.get("Model") == "Spot"
    assert blue.get("ColorValue") == "100 60 0 5"


def test_graphic_keeps_builtins_and_replaces():
    seed = read_part(SEED, "Resources/Graphic.xml")
    out = graphic_xml(graphic_xml(seed, [ORANGE]), [ORANGE])
    names = colour_names(out)
    assert names.count("Brand Orange") == 1
    assert "AID Process" not in names
    assert "AID Spot" not in names
    for name in colour_names(seed):
        if name not in ("AID Process", "AID Spot"):
            assert name in names


def test_styles():
    style = ParaStyle("headline", "Nimbus Sans", "Regular", 48, 57.6, -20.000000000000004, "left", "Ink")
    out = styles_xml(read_part(SEED, "Resources/Styles.xml"), [style])
    p = para(out, "headline")
    assert p.get("Self") == "ParagraphStyle/headline"
    assert p.get("PointSize") == "48"
    assert p.get("Tracking") == "-20"
    assert p.get("Justification") == "LeftAlign"
    assert p.get("FontStyle") == "Regular"
    assert p.get("FillColor") == "Color/Ink"
    assert p.find("Properties/AppliedFont").text == "Nimbus Sans"
    assert p.find("Properties/Leading").text == "57.6"
    names = {s.get("Name") for s in ET.fromstring(out).iter("CharacterStyle")}
    assert {"Bold", "Italic", "BoldItalic"} <= names


def test_style_alignment_and_default_fill():
    seed = read_part(SEED, "Resources/Styles.xml")
    for align, value in [("center", "CenterAlign"), ("right", "RightAlign"), ("justify", "LeftJustified")]:
        out = styles_xml(seed, [ParaStyle("s", "Nimbus Sans", "Regular", 12, 14.4, 0, align, None)])
        assert para(out, "s").get("Justification") == value
        assert para(out, "s").get("FillColor") == "Color/Black"


def test_fonts():
    out = fonts_xml(read_part(SEED, "Resources/Fonts.xml"), {"Nimbus Sans": ["Regular", "Bold"]})
    families = [f for f in ET.fromstring(out).iter("FontFamily") if f.get("Name") == "Nimbus Sans"]
    assert len(families) == 1
    assert {f.get("FontStyleName") for f in families[0].iter("Font")} == {"Regular", "Bold"}
