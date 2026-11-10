"""Report what a built IDML holds, read from the file itself.

    python3 readback.py BUILD.idml [--scale 0.25]

For each page: its size and bleed. For each frame, by its makeready id: kind,
bounding box from the page's top-left corner, its text, the number of outlines
when it has several, each outline's box, the placed image's box and effective
resolution, and whether it is non-printing. Lengths are inches; with --scale
(0.25 for a 1:4 layout) they are full-size inches. Standard library only.
"""

import argparse
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from urllib.parse import unquote

_FRAMES = {"Rectangle", "Polygon", "Oval", "TextFrame", "GraphicLine"}


def _matrix(text):
    return [float(v) for v in text.split()]


def _then(a, b):
    """The transform that applies a, then b."""
    return [
        a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3],
        a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
        a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5],
    ]


def _inverse(m):
    a, b, c, d, e, f = m
    det = a * d - b * c
    return [d / det, -b / det, -c / det, a / det, (c * f - d * e) / det, (b * e - a * f) / det]


def _apply(m, x, y):
    return m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]


def _box(m, points):
    xs, ys = zip(*(_apply(m, x, y) for x, y in points))
    return min(xs), min(ys), max(xs), max(ys)


def _num(value):
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


class _Reader:
    def __init__(self, idml, scale):
        self.zip = zipfile.ZipFile(idml)
        self.unit = 72 * scale  # points per reported inch

    def inches(self, box):
        x0, y0, x1, y1 = (v / self.unit for v in box)
        return f"x {_num(x0)} y {_num(y0)} w {_num(x1 - x0)} h {_num(y1 - y0)}"

    def bleed(self):
        prefs = ET.fromstring(self.zip.read("Resources/Preferences.xml"))
        pref = prefs.find("DocumentPreference")
        top = pref.get("DocumentBleedTopOffset", "0") if pref is not None else "0"
        return float(top)

    def story(self, story_id):
        name = f"Stories/Story_{story_id}.xml"
        if name not in self.zip.namelist():
            return ""
        root = ET.fromstring(self.zip.read(name))
        parts = []
        for node in root.iter():
            if node.tag == "Content":
                parts.append(node.text or "")
            elif node.tag == "Br":
                parts.append(" / ")
        return "".join(parts).strip(" /")

    def spreads(self):
        design = self.zip.read("designmap.xml").decode("utf-8")
        for name in re.findall(r'<idPkg:Spread src="([^"]+)"', design):
            yield ET.fromstring(self.zip.read(name)).find("Spread")


def _outlines(item):
    out = []
    for path in item.findall("Properties/PathGeometry/GeometryPathType"):
        anchors = path.findall("PathPointArray/PathPointType")
        out.append([tuple(map(float, p.get("Anchor").split())) for p in anchors])
    return out


def _label(item):
    for pair in item.findall("Properties/Label/KeyValuePair"):
        if pair.get("Key") == "aid.id":
            return pair.get("Value")
    return item.get("Name") or item.get("Self")


def _frame_lines(reader, item, to_page):
    m = _then(_matrix(item.get("ItemTransform", "1 0 0 1 0 0")), to_page)
    outlines = [o for o in _outlines(item) if o]
    head = f"  {_label(item)}  {item.tag}"
    if outlines:
        head += "  " + reader.inches(_box(m, [p for o in outlines for p in o]))
    if item.tag == "TextFrame":
        text = reader.story(item.get("ParentStory")).replace("\n", " ").replace("\r", " ")
        head += f'  "{text[:100]}..."' if len(text) > 100 else f'  "{text}"'
    if len(outlines) > 1:
        head += f"  {len(outlines)} outlines"
    if item.get("Nonprinting") == "true":
        head += "  nonprinting"
    lines = [head]
    if len(outlines) > 1:
        for n, outline in enumerate(outlines, 1):
            lines.append(f"      outline {n}  {reader.inches(_box(m, outline))}")
    for graphic in [g for tag in ("Image", "PDF", "EPS") for g in item.findall(tag)]:
        lines.append(_graphic_line(reader, graphic, m))
    return lines


def _graphic_line(reader, graphic, frame_to_page):
    m = _then(_matrix(graphic.get("ItemTransform", "1 0 0 1 0 0")), frame_to_page)
    link = graphic.find("Link")
    name = unquote(link.get("LinkResourceURI", "")).rsplit("/", 1)[-1] if link is not None else "(no link)"
    bounds = graphic.find("Properties/GraphicBounds")
    if bounds is None:
        return f"      {graphic.tag.lower()} {name}"
    left, top, right, bottom = (float(bounds.get(k)) for k in ("Left", "Top", "Right", "Bottom"))
    box = _box(m, [(left, top), (right, bottom), (left, bottom), (right, top)])
    line = f"      {'image' if graphic.tag == 'Image' else graphic.tag.lower()} {name}  {reader.inches(box)}"
    width_in = (box[2] - box[0]) / reader.unit
    if graphic.tag == "Image" and width_in > 0:
        line += f"  {_num((right - left) / width_in)} ppi"
    return line


def report(idml, scale=1.0):
    reader = _Reader(idml, scale)
    bleed = reader.bleed()
    lines = []
    number = 0
    for spread in reader.spreads():
        pages = spread.findall("Page")
        for page in pages:
            number += 1
            _, _, height, width = (float(v) for v in page.get("GeometricBounds").split())
            lines.append(
                f"page {number}: {_num(width / reader.unit)} x {_num(height / reader.unit)} in "
                f"({_num(width)} x {_num(height)} pt), bleed {_num(bleed / reader.unit)} in"
            )
        # Frames are measured from the first page on the spread.
        to_page = _inverse(_matrix(pages[0].get("ItemTransform"))) if pages else [1, 0, 0, 1, 0, 0]
        for item in spread:
            if item.tag in _FRAMES:
                lines.extend(_frame_lines(reader, item, to_page))
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(description="Report what a built IDML holds.")
    parser.add_argument("idml")
    parser.add_argument("--scale", type=float, default=1.0, help="layout scale, 0.25 for 1:4")
    args = parser.parse_args(argv)
    if args.scale <= 0:
        parser.error("--scale must be above 0")
    print("\n".join(report(args.idml, args.scale)))


if __name__ == "__main__":
    sys.exit(main())
