import json
import os
import shutil
from pathlib import Path

import pytest
import yaml

from makeready.cli import main
from makeready.figma import FigmaError
from makeready.importer import import_figma

FIX = Path(__file__).parent / "fixtures"
SWATCHES = """
swatches:
  - {name: Brand Orange, hex: "#F26B21", cmyk: [0, 70, 95, 0], pantone: PANTONE 1585 C}
  - {name: Brand Blue, hex: "#0055A5", cmyk: [100, 60, 0, 5], pantone: PANTONE 2935 C, spot: true}
  - {name: Ink, hex: "#1A1A1A", cmyk: [0, 0, 0, 90]}
"""


class FakeClient:
    def __init__(self):
        self.doc = json.loads((FIX / "figma" / "flyer.json").read_text())
        self.calls = []

    def get_file(self, key, version=None):
        self.calls.append(("get_file", key, version))
        return self.doc

    def render(self, key, ids, fmt, version, scale=1):
        self.calls.append(("render", key, tuple(ids), fmt, version))
        return {i: f"https://x/{fmt}/{i}" for i in ids}

    def image_fills(self, key):
        self.calls.append(("image_fills", key))
        return {"img-hero": "https://x/fill/hero", "img-texture": "https://x/fill/texture"}

    def download(self, url, dest):
        self.calls.append(("download", url, dest.name))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"%PDF-1.4" if "/pdf/" in url else b"\x89PNG\r\n\x1a\n")


def repo_with_brand(tmp_path):
    repo = tmp_path / "repo"
    (repo / "brand").mkdir(parents=True)
    (repo / "brand" / "swatches.yaml").write_text(SWATCHES)
    return repo


def template(repo):
    return json.loads((repo / "templates" / "flyer" / "template.json").read_text())


def element(t, eid):
    return next(e for p in t["pages"] for e in p["elements"] if e["id"] == eid)


def test_writes_template(tmp_path):
    repo = repo_with_brand(tmp_path)
    report = import_figma(repo, FakeClient(), "KEY", "flyer")
    t = template(repo)
    assert t["figma"] == {"key": "KEY", "version": "4242"}
    assert (repo / "templates/flyer/reference/Flyer Front.png").exists()
    assert (repo / "templates/flyer/reference/Flyer Back.png").exists()
    assert (repo / "templates/flyer/report.txt").read_text().splitlines() == report


def test_one_render_call_per_format(tmp_path):
    client = FakeClient()
    import_figma(repo_with_brand(tmp_path), client, "KEY", "flyer")
    renders = [c for c in client.calls if c[0] == "render"]
    assert renders == [("render", "KEY", ("1:1", "2:1"), "png", "4242"), ("render", "KEY", ("1:6",), "pdf", "4242")]
    assert [c for c in client.calls if c[0] == "image_fills"] == [("image_fills", "KEY")]


def test_unmapped_vector_exported(tmp_path):
    repo = repo_with_brand(tmp_path)
    report = import_figma(repo, FakeClient(), "KEY", "flyer")
    assert element(template(repo), "Logo")["asset"] == "templates/flyer/assets/Logo.pdf"
    assert (repo / "templates/flyer/assets/Logo.pdf").exists()
    assert (
        "warning: Logo: vector exported from Figma as RGB PDF; map a print-ready file in brand/asset-map.yaml"
    ) in report


def test_mapped_assets(tmp_path):
    repo = repo_with_brand(tmp_path)
    (repo / "brand" / "asset-map.yaml").write_text(
        "assets:\n  Logo: brand/assets/logo.pdf\n  Texture: brand/assets/texture.tif\n"
    )
    client = FakeClient()
    import_figma(repo, client, "KEY", "flyer")
    assert [c[3] for c in client.calls if c[0] == "render"] == ["png"]
    assert not [c for c in client.calls if c[0] == "image_fills"]
    t = template(repo)
    assert element(t, "Logo")["asset"] == "brand/assets/logo.pdf"
    assert element(t, "Texture")["asset"] == "brand/assets/texture.tif"


def test_fixed_image_downloaded(tmp_path):
    repo = repo_with_brand(tmp_path)
    import_figma(repo, FakeClient(), "KEY", "flyer")
    assert element(template(repo), "Texture")["asset"] == "templates/flyer/assets/Texture.png"
    assert "asset" not in element(template(repo), "photo:hero")


def test_cache(tmp_path):
    repo = repo_with_brand(tmp_path)
    import_figma(repo, FakeClient(), "KEY", "flyer")
    assert (repo / "templates/flyer/.figma-cache/4242.json").exists()
    client = FakeClient()
    import_figma(repo, client, "KEY", "flyer", version="4242")
    assert not [c for c in client.calls if c[0] == "get_file"]


def test_removed_slot_reported(tmp_path):
    repo = repo_with_brand(tmp_path)
    old = {"name": "flyer", "pages": [{"elements": [{"id": "subhead", "slot": "subhead"}]}]}
    (repo / "templates/flyer").mkdir(parents=True)
    (repo / "templates/flyer/template.json").write_text(json.dumps(old))
    series = repo / "series" / "spring"
    series.mkdir(parents=True)
    (series / "series.yaml").write_text(yaml.safe_dump({"template": "flyer", "defaults": {"slots": {"subhead": "x"}}}))
    (series / "promo.yaml").write_text(yaml.safe_dump({"slots": {"subhead": "y"}}))
    (series / "other.yaml").write_text(yaml.safe_dump({"template": "poster", "slots": {"subhead": "z"}}))
    report = import_figma(repo, FakeClient(), "KEY", "flyer")
    assert "error: series/spring/series.yaml uses removed slot subhead" in report
    assert "error: series/spring/promo.yaml uses removed slot subhead" in report
    assert not [line for line in report if "other.yaml" in line]


def _find(node, pred, parent=None):
    if isinstance(node, dict):
        if pred(node):
            return parent, node
        for value in node.values():
            found = _find(value, pred, node)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find(item, pred, parent)
            if found:
                return found
    return None


def _clone_after(doc, source_id, new_id, new_name):
    parent, node = _find(doc, lambda item: item.get("id") == source_id)
    copy = json.loads(json.dumps(node))
    copy["id"] = new_id
    copy["name"] = new_name
    children = parent["children"]
    children.insert(children.index(node) + 1, copy)
    return copy


@pytest.mark.parametrize("name", ["a/b", "a\\b", ".", "..", ""])
def test_unsafe_page_name_rejected_before_download(tmp_path, name):
    client = FakeClient()
    frame = client.doc["document"]["children"][0]["children"][0]
    frame["name"] = name
    with pytest.raises(ValueError, match="rename the frame in Figma") as caught:
        import_figma(repo_with_brand(tmp_path), client, "KEY", "flyer")
    assert (name or frame["id"]) in str(caught.value)
    assert [call for call in client.calls if call[0] == "download"] == []


def test_vector_asset_names_are_safe_and_unique(tmp_path):
    client = FakeClient()
    _find(client.doc, lambda item: item.get("id") == "1:6")[1]["name"] = "Logo/Mark"
    _clone_after(client.doc, "1:6", "1:60", "Logo-Mark")
    slash = _find(client.doc, lambda item: item.get("id") == "1:8")[1]
    slash["name"] = "A\\B"
    slash["type"] = "VECTOR"
    repo = repo_with_brand(tmp_path)
    import_figma(repo, client, "KEY", "flyer")
    t = template(repo)
    assert element(t, "Logo/Mark")["asset"] == "templates/flyer/assets/Logo-Mark.pdf"
    assert element(t, "Logo-Mark")["asset"] == "templates/flyer/assets/Logo-Mark-2.pdf"
    assert element(t, "A\\B")["id"] == "A\\B"
    assert element(t, "A\\B")["asset"] == "templates/flyer/assets/A-B.pdf"
    assets = repo / "templates/flyer/assets"
    assert (assets / "Logo-Mark.pdf").is_file()
    assert (assets / "Logo-Mark-2.pdf").is_file()
    assert (assets / "A-B.pdf").is_file()
    assert not (assets / "Logo").exists()


def test_image_asset_names_are_safe_and_unique(tmp_path):
    client = FakeClient()
    _find(client.doc, lambda item: item.get("id") == "1:10")[1]["name"] = "Tex/ture"
    _clone_after(client.doc, "1:10", "1:70", "Tex-ture")
    _find(client.doc, lambda item: item.get("id") == "1:3")[1]["name"] = ".."
    repo = repo_with_brand(tmp_path)
    import_figma(repo, client, "KEY", "flyer")
    t = template(repo)
    assert element(t, "Tex/ture")["id"] == "Tex/ture"
    assert element(t, "Tex/ture")["asset"] == "templates/flyer/assets/Tex-ture.png"
    assert element(t, "Tex-ture")["asset"] == "templates/flyer/assets/Tex-ture-2.png"
    assert element(t, "..")["asset"] == "templates/flyer/assets/...png"
    assets = repo / "templates/flyer/assets"
    assert (assets / "Tex-ture.png").is_file()
    assert (assets / "Tex-ture-2.png").is_file()
    assert (assets / "...png").is_file()
    assert not (repo / "templates/flyer.png").exists()


class FailingClient(FakeClient):
    def __init__(self, fail_on):
        super().__init__()
        self.fail_on = fail_on
        self.downloads = 0

    def download(self, url, dest):
        self.downloads += 1
        if self.downloads >= self.fail_on:
            raise FigmaError(f"download failed: {url}")
        super().download(url, dest)


def _snapshot(root: Path):
    rows = [(".", "dir", root.lstat().st_mtime_ns)]
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        rel = path.relative_to(root).as_posix()
        mtime = path.lstat().st_mtime_ns
        if path.is_dir():
            rows.append((rel, "dir", mtime))
        else:
            rows.append((rel, path.read_bytes(), mtime))
    return rows


def test_failed_download_leaves_template_unchanged(tmp_path):
    repo = repo_with_brand(tmp_path)
    import_figma(repo, FakeClient(), "KEY", "flyer")
    template_dir = repo / "templates" / "flyer"
    (template_dir / "template-overrides.yaml").write_text("keep: yes\n")
    before = _snapshot(template_dir)
    names = sorted(path.name for path in (repo / "templates").iterdir())
    client = FailingClient(fail_on=2)
    with pytest.raises(FigmaError, match="download failed"):
        import_figma(repo, client, "KEY", "flyer")
    assert client.downloads >= 2
    assert _snapshot(template_dir) == before
    assert sorted(path.name for path in (repo / "templates").iterdir()) == names


def test_failed_publish_restores_template(tmp_path, monkeypatch):
    repo = repo_with_brand(tmp_path)
    import_figma(repo, FakeClient(), "KEY", "flyer")
    template_dir = repo / "templates" / "flyer"
    before = _snapshot(template_dir)
    real_move = shutil.move
    failed = []

    def move(src, dest):
        if Path(dest) == template_dir / "assets" and not failed:
            failed.append(src)
            raise OSError("disk error")
        return real_move(src, dest)

    monkeypatch.setattr("makeready.importer.shutil.move", move)
    with pytest.raises(ValueError, match="disk error"):
        import_figma(repo, FakeClient(), "KEY", "flyer", scale=0.5)
    # Moving entries in and out changes the template directory's own mtime.
    after = _snapshot(template_dir)
    assert after[0][:2] == before[0][:2] == (".", "dir")
    assert after[1:] == before[1:]
    assert sorted(path.name for path in (repo / "templates").iterdir()) == ["flyer"]


def test_failed_first_publish_removes_new_paths(tmp_path, monkeypatch):
    repo = repo_with_brand(tmp_path)
    template_dir = repo / "templates" / "flyer"
    real_replace = os.replace

    def replace(src, dest):
        if Path(dest) == template_dir / "template.json":
            raise OSError("disk full")
        return real_replace(src, dest)

    monkeypatch.setattr("makeready.importer.os.replace", replace)
    with pytest.raises(ValueError, match="disk full"):
        import_figma(repo, FakeClient(), "KEY", "flyer")
    assert not (template_dir / "reference").exists()
    assert not (template_dir / "assets").exists()
    assert not (template_dir / "report.txt").exists()
    assert not (template_dir / "template.json").exists()


def test_failed_template_write_keeps_old_template(tmp_path, monkeypatch):
    repo = repo_with_brand(tmp_path)
    import_figma(repo, FakeClient(), "KEY", "flyer")
    template_dir = repo / "templates" / "flyer"
    before = _snapshot(template_dir)
    real_write = Path.write_text

    def write(self, text, *args, **kwargs):
        if self.parent == template_dir and "template.json" in self.name:
            real_write(self, text[:10], *args, **kwargs)
            raise OSError("disk full")
        return real_write(self, text, *args, **kwargs)

    real_replace = os.replace

    def replace(src, dest):
        if Path(dest) == template_dir / "template.json":
            raise OSError("disk full")
        return real_replace(src, dest)

    monkeypatch.setattr(Path, "write_text", write)
    monkeypatch.setattr("makeready.importer.os.replace", replace)
    with pytest.raises(ValueError, match="disk full"):
        import_figma(repo, FakeClient(), "KEY", "flyer", scale=0.5)
    after = _snapshot(template_dir)
    assert after[0][:2] == before[0][:2] == (".", "dir")
    assert after[1:] == before[1:]


def test_failed_restore_keeps_previous_files(tmp_path, monkeypatch):
    repo = repo_with_brand(tmp_path)
    import_figma(repo, FakeClient(), "KEY", "flyer")
    template_dir = repo / "templates" / "flyer"
    logo = (template_dir / "assets" / "Logo.pdf").read_bytes()
    real_move = shutil.move

    def move(src, dest):
        if Path(dest) == template_dir / "assets":
            raise OSError("disk error")
        return real_move(src, dest)

    monkeypatch.setattr("makeready.importer.shutil.move", move)
    with pytest.raises(ValueError, match="kept in") as caught:
        import_figma(repo, FakeClient(), "KEY", "flyer", scale=0.5)
    kept = Path(str(caught.value).rsplit("kept in ", 1)[1])
    assert (kept / "assets" / "Logo.pdf").read_bytes() == logo


def test_reimport_replaces_assets_and_keeps_sidecars(tmp_path):
    repo = repo_with_brand(tmp_path)
    import_figma(repo, FakeClient(), "KEY", "flyer")
    template_dir = repo / "templates" / "flyer"
    (template_dir / "template-overrides.yaml").write_text("keep: yes\n")
    (template_dir / "assets" / "stale.pdf").write_bytes(b"old")
    (template_dir / "reference" / "stale.png").write_bytes(b"old")
    cache = (template_dir / ".figma-cache" / "4242.json").read_bytes()
    import_figma(repo, FakeClient(), "KEY", "flyer")
    assert (template_dir / "template-overrides.yaml").read_text() == "keep: yes\n"
    assert not (template_dir / "assets" / "stale.pdf").exists()
    assert not (template_dir / "reference" / "stale.png").exists()
    assert (template_dir / "assets" / "Logo.pdf").is_file()
    assert (template_dir / "reference" / "Flyer Front.png").is_file()
    assert (template_dir / ".figma-cache" / "4242.json").read_bytes() == cache
    assert sorted(path.name for path in (repo / "templates").iterdir()) == ["flyer"]


def test_reimport_keeps_hand_added_template_keys(tmp_path):
    repo = repo_with_brand(tmp_path)
    import_figma(repo, FakeClient(), "KEY", "flyer")
    template_dir = repo / "templates" / "flyer"
    edited = template(repo)
    edited.update(hooks="hooks.py", bleed=36, notes="hand-set")
    edited["pages"] = []
    edited["figma"] = {"key": "OLD", "version": "1"}
    (template_dir / "template.json").write_text(json.dumps(edited))
    (template_dir / "hooks.py").write_text("def item(element, frame, context):\n    return None\n")
    import_figma(repo, FakeClient(), "KEY", "flyer")
    t = template(repo)
    assert (t["hooks"], t["bleed"], t["notes"]) == ("hooks.py", 36, "hand-set")
    assert t["pages"] and t["figma"] == {"key": "KEY", "version": "4242"}
    assert (template_dir / "hooks.py").is_file()


def test_template_json_written_last(tmp_path, monkeypatch):
    repo = repo_with_brand(tmp_path)
    import_figma(repo, FakeClient(), "KEY", "flyer")
    live = repo / "templates" / "flyer"
    events = []
    real_write = Path.write_text
    real_replace = os.replace

    def spy(self, text, *args, **kwargs):
        if self == live or live in self.parents:
            events.append(self.relative_to(live).as_posix())
        return real_write(self, text, *args, **kwargs)

    def replace_spy(src, dest):
        dest = Path(dest)
        if dest == live or live in dest.parents:
            events.append(dest.relative_to(live).as_posix())
        return real_replace(src, dest)

    monkeypatch.setattr(Path, "write_text", spy)
    monkeypatch.setattr("makeready.importer.os.replace", replace_spy)
    import_figma(repo, FakeClient(), "KEY", "flyer", scale=0.5)
    assert "report.txt" in events
    assert events.index("report.txt") < events.index("template.json")
    assert events[-1] == "template.json"


def _config(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[figma]\ntoken = "token"\n'
        '[mac]\nhost = "h"\nhome = "/Users/j"\n'
        f'[assets]\nphotos = "{tmp_path}"\nfonts = "{tmp_path}"\n'
    )
    monkeypatch.setenv("MAKEREADY_CONFIG", str(cfg))


def test_cli_reports_importer_value_error(tmp_path, monkeypatch, capsys):
    _config(tmp_path, monkeypatch)
    repo = repo_with_brand(tmp_path)
    assert main(["--repo", str(repo), "import-figma", "KEY", "--name", "a/b"]) == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "a/b" in err


def test_cli_reports_extractor_value_error(tmp_path, monkeypatch, capsys):
    _config(tmp_path, monkeypatch)
    repo = repo_with_brand(tmp_path)
    document = json.loads((FIX / "figma" / "flyer.json").read_text())
    document["document"]["children"][0]["children"][0]["children"][0]["style"]["textAutoResize"] = ["HEIGHT"]
    cache = repo / "templates" / "flyer" / ".figma-cache"
    cache.mkdir(parents=True)
    (cache / "4242.json").write_text(json.dumps(document))
    code = main(["--repo", str(repo), "import-figma", "KEY", "--name", "flyer", "--version", "4242"])
    assert code == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "headline" in err
    assert "textAutoResize" in err


def test_cli_requires_token(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'[mac]\nhost = "h"\nhome = "/Users/j"\n[assets]\nphotos = "{tmp_path}"\nfonts = "{tmp_path}"\n')
    monkeypatch.setenv("MAKEREADY_CONFIG", str(cfg))
    repo = repo_with_brand(tmp_path)
    shutil.rmtree(repo / "brand")
    assert main(["--repo", str(repo), "import-figma", "KEY", "--name", "flyer"]) == 2
    assert "figma.token" in capsys.readouterr().err
