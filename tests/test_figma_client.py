import io
import json
import urllib.error

import pytest

from makeready.figma import FigmaClient, FigmaError, cached_file


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Opener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return Resp(r if isinstance(r, bytes) else json.dumps(r).encode())


def http_error(code):
    return urllib.error.HTTPError("https://api.figma.com", code, "error", {}, None)


def test_get_file_uses_geometry_paths():
    op = Opener({"version": "123", "document": {}})
    assert FigmaClient("tok", opener=op).get_file("KEY")["version"] == "123"
    req = op.requests[0]
    assert req.full_url == "https://api.figma.com/v1/files/KEY?geometry=paths"
    assert req.get_header("X-figma-token") == "tok"


def test_get_file_pinned_version():
    op = Opener({"version": "9"})
    FigmaClient("tok", opener=op).get_file("KEY", version="9")
    assert op.requests[0].full_url == "https://api.figma.com/v1/files/KEY?geometry=paths&version=9"


def test_render_batches_ids():
    op = Opener({"images": {"1:2": "https://x/a.png", "3:4": "https://x/b.png"}})
    urls = FigmaClient("tok", opener=op).render("KEY", ["1:2", "3:4"], "png", "9")
    assert urls == {"1:2": "https://x/a.png", "3:4": "https://x/b.png"}
    assert op.requests[0].full_url == (
        "https://api.figma.com/v1/images/KEY?ids=1%3A2%2C3%3A4&format=png&version=9&scale=1"
    )


def test_image_fills():
    op = Opener({"meta": {"images": {"ref1": "https://x/r1"}}})
    assert FigmaClient("tok", opener=op).image_fills("KEY") == {"ref1": "https://x/r1"}
    assert op.requests[0].full_url == "https://api.figma.com/v1/files/KEY/images"


def test_download(tmp_path):
    op = Opener(b"PNGDATA")
    dest = tmp_path / "a" / "b.png"
    FigmaClient("tok", opener=op).download("https://x/a.png", dest)
    assert dest.read_bytes() == b"PNGDATA"
    assert op.requests[0].get_header("X-figma-token") is None


@pytest.mark.parametrize("code, message", [(403, "expired"), (429, "rate limit"), (500, "HTTP 500")])
def test_errors(code, message):
    op = Opener(http_error(code))
    with pytest.raises(FigmaError, match=message):
        FigmaClient("tok", opener=op).get_file("KEY")


def test_no_token():
    with pytest.raises(FigmaError, match="figma.token"):
        FigmaClient(None)


def test_cached_file(tmp_path):
    op = Opener({"version": "77", "name": "f"})
    client = FigmaClient("tok", opener=op)
    doc = cached_file(client, "KEY", tmp_path)
    assert json.loads((tmp_path / "77.json").read_text()) == doc
    assert cached_file(client, "KEY", tmp_path, version="77") == doc
    assert len(op.requests) == 1
