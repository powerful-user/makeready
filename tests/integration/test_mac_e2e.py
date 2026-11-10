import dataclasses
import os
import shutil
import subprocess

import pytest
from fixture_repo import FIXTURES, make_repo
from PIL import Image

from makeready.approve import approve
from makeready.build import build
from makeready.colourcheck import check
from makeready.config import load
from makeready.render import render
from makeready.spec import resolve

pytestmark = pytest.mark.skipif(
    os.environ.get("MAKEREADY_MAC_E2E") != "1", reason="needs the real Mac; run with MAKEREADY_MAC_E2E=1"
)
SERIES = "aid-e2e"


def _photos(folder):
    """A photo with real tonal variation; InDesign exports a near-flat one as an indexed image."""
    folder.mkdir()
    size = (2700, 1800)
    across = Image.linear_gradient("L").rotate(90).resize(size)
    down = Image.linear_gradient("L").resize(size)
    ring = Image.radial_gradient("L").resize(size)
    photo = Image.merge("RGB", (across, down, ring))
    assert photo.getcolors(4096) is None
    photo.save(folder / "kitchen.jpg", dpi=(300, 300), quality=90)
    return folder


@pytest.fixture(scope="module")
def flow(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("e2e")
    repo = make_repo(tmp, real_seed=True)
    shutil.move(repo / "series" / "spring", repo / "series" / SERIES)
    cfg = dataclasses.replace(
        load(), photos=_photos(tmp / "photos"), fonts=FIXTURES / "fonts",
        print_inks=("C", "M", "Y", "K", "Brand Blue"), cache_dir=tmp / "cache",
    )
    built = build(repo, cfg, SERIES, "garden")
    result = render(repo, cfg, SERIES, "garden")
    return repo, cfg, built, result, tmp


def test_render_clean(flow):
    _, _, built, result, _ = flow
    assert result.status in ("ok", "warnings"), result.message
    assert result.build_id == built.build_id
    assert result.report["overset"] == []
    assert result.report["missing_links"] == []
    assert result.report["missing_fonts"] == []


def test_pdf_is_pdfx4_and_colour_clean(flow):
    _, cfg, _, result, tmp = flow
    meta = subprocess.run(["pdfinfo", "-meta", str(result.pdf)], capture_output=True, text=True).stdout
    assert "PDF/X-4" in meta
    assert check(result.pdf, cfg.print_inks, tmp / "colour") == []


def test_approve_packages(flow):
    repo, cfg, _, _, _ = flow
    approve(repo, cfg, SERIES, "garden")
    assert resolve(repo, SERIES, "garden").status == "approved"
