import json
from pathlib import Path

import pytest

from makeready.colour import Swatch
from makeready.extract import extract

FIX = Path(__file__).parent / "fixtures"
SWATCHES = [
    Swatch("Brand Orange", "#F26B21", (0, 70, 95, 0), False, "PANTONE 1585 C"),
    Swatch("Brand Blue", "#0055A5", (100, 60, 0, 5), True, "PANTONE 2935 C"),
    Swatch("Ink", "#1A1A1A", (0, 0, 0, 90), False, None),
]


def doc():
    return json.loads((FIX / "figma" / "flyer.json").read_text())


def elements(template, page=0):
    return {e["id"]: e for e in template["pages"][page]["elements"]}


def test_pages():
    t, _ = extract(doc(), "flyer", SWATCHES)
    assert t["name"] == "flyer"
    assert t["scale"] == 1.0
    assert t["bleed"] == 9.0
    assert [(p["name"], p["figma_id"], p["width"], p["height"]) for p in t["pages"]] == [
        ("Flyer Front", "1:1", 612, 792),
        ("Flyer Back", "2:1", 612, 792),
    ]


def _layers(names):
    children = []
    for index, name in enumerate(names, start=2):
        children.append(
            {
                "id": f"1:{index}",
                "type": "RECTANGLE",
                "name": name,
                "size": {"x": 10, "y": 10},
                "relativeTransform": [[1, 0, 0], [0, 1, 0]],
                "fills": [],
            }
        )
    return {
        "document": {
            "id": "0:0",
            "type": "DOCUMENT",
            "children": [
                {
                    "id": "0:1",
                    "type": "CANVAS",
                    "name": "Page 1",
                    "children": [
                        {
                            "id": "1:1",
                            "type": "FRAME",
                            "name": "Flyer Front",
                            "size": {"x": 100, "y": 100},
                            "relativeTransform": [[1, 0, 0], [0, 1, 0]],
                            "children": children,
                        }
                    ],
                }
            ],
        }
    }


def test_suffix_skips_a_name_already_taken():
    template, _ = extract(_layers(["Band-2", "Band", "Band"]), "flyer", SWATCHES)
    assert [element["id"] for element in template["pages"][0]["elements"]] == ["Band-2", "Band", "Band-3"]
    template, _ = extract(_layers(["Band-2", "{Band}", "{Band}"]), "flyer", SWATCHES)
    assert [(element["id"], element["slot"]) for element in template["pages"][0]["elements"]] == [
        ("Band-2", None),
        ("Band", "Band"),
        ("Band-3", "Band"),
    ]


def test_order_and_ids():
    t, _ = extract(doc(), "flyer", SWATCHES)
    assert [e["id"] for e in t["pages"][0]["elements"]] == [
        "headline", "photo:hero", "Band", "Logo", "Band-2", "Dot", "Texture",
    ]
    assert [e["id"] for e in t["pages"][1]["elements"]] == ["headline-2"]
    assert elements(t, 1)["headline-2"]["slot"] == "headline"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("textAlignHorizontal", ["LEFT"]),
        ("textAlignVertical", {"TOP": True}),
        ("textAutoResize", ["HEIGHT"]),
    ],
)
def test_text_enum_must_be_text(key, value):
    d = doc()
    d["document"]["children"][0]["children"][0]["children"][0]["style"][key] = value
    with pytest.raises(ValueError, match="headline") as caught:
        extract(d, "flyer", SWATCHES)
    assert key in str(caught.value)


def test_text():
    e = elements(extract(doc(), "flyer", SWATCHES)[0])["headline"]
    assert e["kind"] == "text"
    assert e["slot"] == "headline"
    assert e["transform"] == [1, 0, 0, 1, 36, 48]
    assert (e["width"], e["height"]) == (400, 60)
    assert e["text"] == {
        "characters": "Open House", "font": "Nimbus Sans/Regular", "size": 48,
        "tracking": pytest.approx(-20), "leading": 57.6, "align": "left", "valign": "top",
        "auto_height": True,
    }
    assert e["fill"] == {"swatch": "Ink", "opacity": 1}
    assert e["opacity"] == 1


def test_rotated_shape():
    e = elements(extract(doc(), "flyer", SWATCHES)[0])["Band"]
    assert e["kind"] == "shape"
    assert e["shape"] == "rect"
    assert e["slot"] is None
    assert e["transform"] == pytest.approx([0.8660254, 0.5, -0.5, 0.8660254, 300, 100])
    assert e["opacity"] == 0.8
    assert e["fill"] == {"swatch": "Brand Orange", "opacity": 0.5}
    assert e["stroke"] == {"swatch": "Brand Blue", "opacity": 1, "weight": 2}


def test_group_composes_transform():
    e = elements(extract(doc(), "flyer", SWATCHES)[0])["Logo"]
    assert e["kind"] == "vector"
    assert e["transform"] == [1, 0, 0, 1, 36, 720]
    assert e["fill"] == {"swatch": "Brand Blue", "opacity": 1}


def test_images():
    els = elements(extract(doc(), "flyer", SWATCHES)[0])
    assert els["photo:hero"]["kind"] == "image"
    assert els["photo:hero"]["slot"] == "photo:hero"
    assert els["photo:hero"]["image"] == {"ref": "img-hero", "scale_mode": "FILL"}
    assert els["Texture"]["image"]["ref"] == "img-texture"
    assert els["Texture"]["slot"] is None


def test_report():
    _, report = extract(doc(), "flyer", SWATCHES)
    assert report == [
        "warning: Band-2: colour #33CC33 matches no brand swatch",
        "warning: Band-2: effect DROP_SHADOW left out",
        "warning: Dot: gradient fill is not supported",
        "warning: Texture: blend mode MULTIPLY left out",
        "warning: Flyer Back: frame clips content; drawn bleed will be lost",
    ]


def test_scale():
    t, _ = extract(doc(), "flyer", SWATCHES, scale=0.5)
    p = t["pages"][0]
    assert (p["width"], p["height"]) == (306, 396)
    e = elements(t)["headline"]
    assert e["transform"] == [1, 0, 0, 1, 18, 24]
    assert (e["width"], e["height"]) == (200, 30)
    assert e["text"]["size"] == 24
    assert e["text"]["leading"] == 28.8
    assert e["text"]["tracking"] == pytest.approx(-20)


def test_frames_filter():
    t, _ = extract(doc(), "flyer", SWATCHES, frames=["Flyer Back"])
    assert [p["name"] for p in t["pages"]] == ["Flyer Back"]
    with pytest.raises(ValueError, match="Nope"):
        extract(doc(), "flyer", SWATCHES, frames=["Nope"])


@pytest.mark.parametrize("name", ["a/b", "a\\b", ".", "..", ""])
def test_unsafe_page_name(name):
    d = doc()
    frame = d["document"]["children"][0]["children"][0]
    frame["name"] = name
    with pytest.raises(ValueError, match="rename the frame in Figma") as caught:
        extract(d, "flyer", SWATCHES)
    assert (name or frame["id"]) in str(caught.value)


def test_duplicate_page_name():
    d = doc()
    front, back = d["document"]["children"][0]["children"][:2]
    back["name"] = front["name"]
    with pytest.raises(ValueError, match="Flyer Front: two frames share this page name"):
        extract(d, "flyer", SWATCHES)


def test_text_runs_and_trim():
    d = doc()
    back = d["document"]["children"][0]["children"][1]["children"][0]
    back["style"]["leadingTrim"] = "CAP_HEIGHT"
    back["characterStyleOverrides"] = [0, 0, 1, 1]
    back["styleOverrideTable"] = {
        "1": {"fills": [{"type": "SOLID", "opacity": 0.5,
                         "color": {"r": 0.101960786, "g": 0.101960786, "b": 0.101960786, "a": 1}}]}
    }
    _, report = extract(d, "flyer", SWATCHES)
    assert "warning: headline-2: text runs with different opacities are not supported" in report
    assert "warning: headline-2: trim leading is not supported" in report
