"""Generate the binary test fixtures. Run from the repository root:

    uv run --offline --no-project --with pillow python tests/fixtures/make_fixtures.py
"""

import shutil
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
URW = Path("/usr/share/fonts/opentype/urw-base35")
FONTS = ["NimbusSans-Regular.otf", "NimbusSans-Bold.otf", "NimbusSans-Italic.otf", "NimbusSans-BoldItalic.otf"]
SPOT = "/CS0 cs 1 scn 72 72 200 200 re f 0 0 0 1 k 300 300 100 100 re f"
CMYK_ONLY = "0 0 0 1 k 72 72 100 100 re f"
LOGO = "/CS0 cs 1 scn 0 0 80 40 re f"


def write_pdf(path, contents, width=612, height=792):
    """A minimal PDF whose pages may use /CS0, the Brand Blue separation."""
    n = len(contents)
    objs = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: "<< /Type /Pages /Kids [%s] /Count %d >>"
        % (" ".join(f"{4 + 2 * i} 0 R" for i in range(n)), n),
        3: "[/Separation /Brand#20Blue /DeviceCMYK << /FunctionType 2 /Domain [0 1] "
        "/C0 [0 0 0 0] /C1 [1 0.6 0 0.05] /N 1 >>]",
    }
    for i, content in enumerate(contents):
        objs[4 + 2 * i] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
            f"/Resources << /ColorSpace << /CS0 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>"
        )
        data = content.encode()
        objs[5 + 2 * i] = b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream"
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        body = objs[num] if isinstance(objs[num], bytes) else objs[num].encode()
        out += b"%d 0 obj\n" % num + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for num in sorted(objs):
        out += b"%010d 00000 n \n" % offsets[num]
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))


def jpeg(path, size, colour, dpi=300):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(path, dpi=(dpi, dpi), quality=90)


def main():
    for folder in ("fonts", "bootstrap"):
        (HERE / folder).mkdir(parents=True, exist_ok=True)
    for name in FONTS:
        shutil.copyfile(URW / name, HERE / "fonts" / name)
    shutil.copyfile(URW / FONTS[0], HERE / "bootstrap" / FONTS[0])
    jpeg(HERE / "photos" / "kitchen.jpg", (2700, 1800), (180, 120, 90))
    jpeg(HERE / "bootstrap" / "ref.jpg", (600, 400), (90, 140, 180))
    write_pdf(HERE / "bootstrap" / "logo.pdf", [LOGO], 80, 40)
    write_pdf(HERE / "pdf" / "spot.pdf", [SPOT])
    write_pdf(HERE / "pdf" / "spot2.pdf", [CMYK_ONLY, SPOT])
    write_pdf(HERE / "repo" / "brand" / "assets" / "logo.pdf", [LOGO], 80, 40)
    ref = HERE / "repo" / "templates" / "flyer" / "reference" / "Flyer Front.png"
    ref.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (612, 792), (230, 230, 230)).save(ref)


if __name__ == "__main__":
    main()
