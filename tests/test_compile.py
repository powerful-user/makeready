import shutil
import struct
import xml.etree.ElementTree as ET
import zipfile

import pytest
from fixture_repo import FIXTURES, make_config, make_repo

from makeready.idml.document import compile_document
from makeready.idml.package import write_package
from makeready.spec import SpecError, list_docs, load_template, resolve


def compiled(tmp_path, doc="garden", edit=None, cfg=None):
    repo = make_repo(tmp_path, real_seed=True)
    if edit:
        edit(repo)
    r = resolve(repo, "spring", doc)
    return compile_document(repo, cfg or make_config(tmp_path), r, load_template(repo, r.template))


def spread(c):
    names = [n for n in c.parts if n.startswith("Spreads/")]
    assert len(names) == 1
    return ET.fromstring(c.parts[names[0]])


def stories(c):
    return [ET.fromstring(c.parts[n]) for n in c.parts if n.startswith("Stories/")]


def text_of(node):
    return "".join(x.text or "" for x in node.iter("Content"))


def by_label(root):
    out = {}
    for item in root.iter():
        kv = item.find("Properties/Label/KeyValuePair")
        if kv is not None and kv.get("Key") == "aid.id":
            out[kv.get("Value")] = item
    return out


def test_parts(tmp_path):
    c = compiled(tmp_path)
    assert {"designmap.xml", "Resources/Graphic.xml", "Resources/Styles.xml", "Resources/Fonts.xml",
            "Resources/Preferences.xml"} <= set(c.parts)
    story_names = [n for n in c.parts if n.startswith("Stories/")]
    assert len(story_names) == 2
    designmap = c.parts["designmap.xml"].decode()
    for name in story_names + [n for n in c.parts if n.startswith("Spreads/")]:
        assert f'src="{name}"' in designmap


def test_page_and_bleed(tmp_path):
    c = compiled(tmp_path)
    dp = next(ET.fromstring(c.parts["Resources/Preferences.xml"]).iter("DocumentPreference"))
    assert dp.get("PageWidth") == "612"
    assert dp.get("PageHeight") == "792"
    assert dp.get("FacingPages") == "false"
    for side in ("Top", "Bottom", "InsideOrLeft", "OutsideOrRight"):
        assert dp.get(f"DocumentBleed{side}Offset") == "9"
    page = next(spread(c).iter("Page"))
    assert page.get("GeometricBounds") == "0 0 792 612"
    assert page.get("ItemTransform") == "1 0 0 1 -306 -396"


def test_item_transform_spread_centre(tmp_path):
    band = by_label(spread(compiled(tmp_path)))["Band"]
    a, b, c, d, e, f = map(float, band.get("ItemTransform").split())
    assert (a, b, c, d) == pytest.approx((0.8660254, 0.5, -0.5, 0.8660254))
    assert (e, f) == pytest.approx((300 - 306, 100 - 396))


def test_text_frame_top(tmp_path):
    tf = by_label(spread(compiled(tmp_path, "open-house")))["headline"]
    assert tf.tag == "TextFrame"
    pref = tf.find("TextFramePreference")
    assert pref.get("FirstBaselineOffset") == "FixedHeight"
    assert float(pref.get("MinimumFirstBaselineOffset")) == pytest.approx((57.6 - 48) / 2 + 0.729 * 48)
    assert pref.get("VerticalJustification") == "TopAlign"
    assert pref.get("AutoSizingType") == "HeightOnly"
    assert pref.get("AutoSizingReferencePoint") == "TopCenterPoint"


def test_text_frame_bottom(tmp_path):
    tf = by_label(spread(compiled(tmp_path, "open-house")))["body"]
    pref = tf.find("TextFramePreference")
    assert pref.get("VerticalJustification") == "BottomAlign"
    assert pref.get("AutoSizingType") == "Off"
    insets = [float(li.text) for li in pref.find("Properties/InsetSpacing")]
    assert insets == pytest.approx([0, 0, 4.452, 0])


def test_story_content(tmp_path):
    c = compiled(tmp_path, "open-house")
    texts = [text_of(s) for s in stories(c)]
    assert "Open House, April 12" in texts
    body = next(s for s in stories(c) if "Join us " in text_of(s))
    runs = [(r.get("AppliedCharacterStyle"), text_of(r)) for r in body.iter("CharacterStyleRange")]
    assert ("CharacterStyle/Bold", "Saturday") in runs
    assert ("CharacterStyle/Italic", "coffee") in runs
    assert sum(1 for _ in body.iter("Br")) == 2
    bullets = [p for p in body.iter("ParagraphStyleRange") if p.get("BulletsAndNumberingListType") == "BulletList"]
    assert len(bullets) == 2


def test_overrides_in_story(tmp_path):
    c = compiled(tmp_path, "garden")
    ranges = [r for s in stories(c) for r in s.iter("CharacterStyleRange") if "Garden Party" in text_of(r)]
    assert ranges[0].get("PointSize") == "54"
    assert ranges[0].get("Tracking") == "-10"
    pref = by_label(spread(c))["headline"].find("TextFramePreference")
    assert float(pref.get("MinimumFirstBaselineOffset")) == pytest.approx((57.6 - 54) / 2 + 0.729 * 54)


def test_transparency_and_colours(tmp_path):
    band = by_label(spread(compiled(tmp_path)))["Band"]
    assert band.find("TransparencySetting/BlendingSetting").get("Opacity") == "90"
    assert band.find("FillTransparencySetting/BlendingSetting").get("Opacity") == "50"
    assert band.get("FillColor") == "Color/Brand Orange"
    assert band.get("StrokeColor") == "Color/Brand Blue"
    assert band.get("StrokeWeight") == "2"


def test_image(tmp_path):
    c = compiled(tmp_path)
    img = by_label(spread(c))["photo:hero"].find("Image")
    gb = img.find("Properties/GraphicBounds")
    assert (gb.get("Right"), gb.get("Bottom")) == ("2700", "1800")
    a, b, cc, d, e, f = map(float, img.get("ItemTransform").split())
    s = max(630 / 648, 400 / 432)
    assert a == pytest.approx(72 / 300 * s)
    assert d == pytest.approx(72 / 300 * s)
    assert e == pytest.approx(0.5 * (630 - 648 * s))
    assert f == pytest.approx(0.35 * (400 - 432 * s))
    assert img.find("Link").get("LinkResourceURI") == "file:/Users/j/makeready/jobs/spring/garden/in/links/kitchen.jpg"
    assert c.links["kitchen.jpg"] == FIXTURES / "photos" / "kitchen.jpg"
    assert "logo.pdf" in c.links
    assert c.fonts == [FIXTURES / "fonts" / n for n in ["NimbusSans-Bold.otf", "NimbusSans-Italic.otf", "NimbusSans-Regular.otf"]]


def test_link_uri_encoding(tmp_path):
    photos = tmp_path / "photos"
    photos.mkdir()
    shutil.copyfile(FIXTURES / "photos" / "kitchen.jpg", photos / "my kitchen.jpg")
    cfg = make_config(tmp_path)
    cfg = type(cfg)(**{**cfg.__dict__, "photos": photos})

    def edit(repo):
        (repo / "series/spring/garden.yaml").write_text("slots:\n  photo:hero: {src: my kitchen.jpg, fit: fill}\n")

    img = by_label(spread(compiled(tmp_path, edit=edit, cfg=cfg)))["photo:hero"].find("Image")
    assert img.find("Link").get("LinkResourceURI").endswith("/in/links/my%20kitchen.jpg")


def test_vector_asset(tmp_path):
    pdf = by_label(spread(compiled(tmp_path)))["Logo"].find("PDF")
    assert pdf is not None
    assert pdf.find("Link").get("LinkResourceURI").endswith("/in/links/logo.pdf")


def test_hidden_override(tmp_path):
    def edit(repo):
        (repo / "series/spring/garden.yaml").write_text("overrides:\n  Logo: {hidden: true}\n")

    assert "Logo" not in by_label(spread(compiled(tmp_path, edit=edit)))


def test_self_ids_unique(tmp_path):
    c = compiled(tmp_path)
    seen = []
    for name, data in c.parts.items():
        if name.startswith(("Spreads/", "Stories/")):
            seen += [el.get("Self") for el in ET.fromstring(data).iter() if el.get("Self")]
    assert len(seen) == len(set(seen))


def break_member(path, name, how):
    blob = bytearray(path.read_bytes())
    with zipfile.ZipFile(path) as package:
        info = package.getinfo(name)
    if how == "crc":
        struct.pack_into("<I", blob, info.header_offset + 14, 0)
        encoded = name.encode("utf-8")
        signature = b"PK\x01\x02"
        index = 0
        while True:
            found = blob.find(signature, index)
            assert found >= 0
            length = struct.unpack_from("<H", blob, found + 28)[0]
            if bytes(blob[found + 46:found + 46 + length]) == encoded:
                struct.pack_into("<I", blob, found + 16, 0)
                break
            index = found + 4
    else:
        name_len, extra_len = struct.unpack_from("<HH", blob, info.header_offset + 26)
        start = info.header_offset + 30 + name_len + extra_len
        blob[start] ^= 0xFF
    path.write_bytes(blob)


def compile_repo(repo, tmp_path):
    resolved = resolve(repo, "spring", "garden")
    return compile_document(repo, make_config(tmp_path), resolved, load_template(repo, resolved.template))


def test_seed_bad_deflate_names_package_and_member(tmp_path):
    repo = make_repo(tmp_path, real_seed=True)
    seed = repo / "seed" / "reference.idml"
    break_member(seed, "designmap.xml", "deflate")
    with pytest.raises(SpecError) as err:
        compile_repo(repo, tmp_path)
    text = str(err.value)
    assert str(seed) in text
    assert "designmap.xml" in text


def test_seed_story_bad_deflate_names_package_and_member(tmp_path):
    repo = make_repo(tmp_path, real_seed=True)
    seed = repo / "seed" / "reference.idml"
    with zipfile.ZipFile(seed) as package:
        story = next(name for name in package.namelist() if name.startswith("Stories/") and name.endswith(".xml"))
    break_member(seed, story, "deflate")
    with pytest.raises(SpecError) as err:
        compile_repo(repo, tmp_path)
    text = str(err.value)
    assert str(seed) in text
    assert story in text


def test_package_self_ids_unique(tmp_path):
    repo = make_repo(tmp_path, real_seed=True)
    cfg = make_config(tmp_path)
    seed = repo / "seed" / "reference.idml"
    docs = list_docs(repo, "spring")
    assert docs
    for doc in docs:
        resolved = resolve(repo, "spring", doc)
        compiled_doc = compile_document(repo, cfg, resolved, load_template(repo, resolved.template))
        out = tmp_path / f"{doc}.idml"
        write_package(seed, out, compiled_doc.parts)
        seen: dict[str, list[str]] = {}
        seed_parts = 0
        with zipfile.ZipFile(out) as package:
            for name in package.namelist():
                if not name.endswith(".xml"):
                    continue
                if name not in compiled_doc.parts:
                    seed_parts += 1
                root = ET.fromstring(package.read(name))
                for element in root.iter():
                    value = element.get("Self")
                    if value is None:
                        continue
                    seen.setdefault(value, []).append(name)
        assert seed_parts, doc
        duplicates = {value: names for value, names in seen.items() if len(names) > 1}
        assert not duplicates, (doc, duplicates)


def test_geometry(tmp_path):
    page = compiled(tmp_path).geometry["pages"][0]
    assert (page["name"], page["width"], page["height"], page["bleed"]) == ("Flyer Front", 612, 792, 9)
    boxes = {e["id"]: e["bbox"] for e in page["elements"]}
    assert boxes["photo:hero"] == pytest.approx([-9, 300, 621, 700])


def test_seed_short_member_names_package_and_member(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, real_seed=True)
    seed = repo / "seed" / "reference.idml"
    original = zipfile.ZipFile.read

    def read(self, member, pwd=None):
        if getattr(member, "filename", member) == "designmap.xml":
            raise EOFError
        return original(self, member, pwd)

    monkeypatch.setattr(zipfile.ZipFile, "read", read)
    with pytest.raises(SpecError) as err:
        compile_repo(repo, tmp_path)
    text = str(err.value)
    assert str(seed) in text
    assert "designmap.xml" in text
