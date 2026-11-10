import json

import pytest
from fakes import Mac
from fixture_repo import FIXTURES, make_config, make_repo
from PIL import Image

from makeready.build import build
from makeready.mac import MacBusy, MacUnreachable
from makeready.render import render, render_many
from makeready.spec import SpecError, set_status
from makeready.state import Busy, is_busy, mark_busy, render_lock


def setup(tmp_path, monkeypatch, **scenario):
    mac = Mac(tmp_path, monkeypatch, **scenario)
    monkeypatch.setenv("FAKE_PDF", str(FIXTURES / "pdf" / "spot.pdf"))
    repo, cfg = make_repo(tmp_path, real_seed=True), make_config(tmp_path)
    return mac, repo, cfg, build(repo, cfg, "spring", "garden")


def go(mac, repo, cfg):
    return render(repo, cfg, "spring", "garden", ssh=mac.ssh, rsync=mac.rsync)


def test_render_ok(tmp_path, monkeypatch):
    mac, repo, cfg, b = setup(tmp_path, monkeypatch)
    res = go(mac, repo, cfg)
    assert res.status == "ok"
    assert res.build_id == b.build_id
    out = repo / "build/spring/garden" / f"{b.build_id}.render"
    assert res.pdf == out / "garden.pdf"
    assert res.pdf.exists()
    assert json.loads((out / "report.json").read_text())["build_id"] == b.build_id
    job = mac.path("/Users/j/makeready/jobs/spring/garden/in")
    assert (job / "garden.idml").exists()
    assert (job / "Document Fonts" / "NimbusSans-Regular.otf").exists()
    assert mac.path("/Users/j/makeready/scripts/render.jsx").exists()


def test_render_order(tmp_path, monkeypatch):
    mac, repo, cfg, b = setup(tmp_path, monkeypatch)
    go(mac, repo, cfg)
    calls = mac.ssh_calls()
    assert "aid-probe" in calls[0]["stdin"]
    last = calls[-1]
    assert last["argv"][-1] == "caffeinate -i osascript -"
    assert "render.jsx" in last["stdin"]
    assert "with timeout of 600 seconds" in last["stdin"]
    assert f'"{b.build_id}"' in last["stdin"]
    assert '"GRACoL2013_CRPC6"' in last["stdin"]
    assert [c[-1] for c in mac.rsync_calls()] == [
        "h:/Users/j/makeready/scripts/",
        "h:/Users/j/makeready/jobs/spring/garden/in/",
        f"{repo / 'build/spring/garden' / (b.build_id + '.render')}/",
    ]


def test_no_report_is_failure(tmp_path, monkeypatch):
    mac, repo, cfg, _ = setup(tmp_path, monkeypatch, render="no_report")
    res = go(mac, repo, cfg)
    assert res.status == "failed"
    assert "no report.json" in res.message


def test_report_field_types(tmp_path):
    from makeready.render import _load_report

    path = tmp_path / "report.json"
    good = {
        "job": "0123456789abcdef-2026-10-18T00:00:00Z",
        "build_id": "0123456789abcdef",
        "status": "ok",
        "overset": [],
        "missing_links": [],
        "missing_fonts": [],
        "inks": ["Process Cyan"],
        "preflight": [{"rule": "Images", "element": None}],
        "error": None,
    }
    path.write_text(json.dumps(good))
    loaded, problem = _load_report(path)
    assert problem is None
    assert loaded["build_id"] == good["build_id"]
    bad = {
        "job": None,
        "build_id": 1,
        "status": ["ok"],
        "overset": "nope",
        "missing_links": [1],
        "missing_fonts": None,
        "inks": {"C": True},
        "preflight": ["Images"],
        "error": 2,
    }
    for field, value in bad.items():
        path.write_text(json.dumps({**good, field: value}))
        _loaded, problem = _load_report(path)
        assert problem is not None, field
        assert field in problem
        assert str(path) in problem


def test_status_list_is_a_failed_render(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch, status=["ok"])
    monkeypatch.setenv("FAKE_PDF", str(FIXTURES / "pdf" / "spot.pdf"))
    repo, cfg = make_repo(tmp_path, real_seed=True), make_config(tmp_path)
    build(repo, cfg, "spring", "garden")
    result = go(mac, repo, cfg)
    assert result.status == "failed"
    assert "status" in result.message
    assert "report.json" in result.message


def test_wrong_build_is_failure(tmp_path, monkeypatch):
    mac, repo, cfg, _ = setup(tmp_path, monkeypatch, render="wrong_build")
    res = go(mac, repo, cfg)
    assert res.status == "failed"
    assert "build" in res.message


def test_bad_json_report_is_kept_as_pulled(tmp_path, monkeypatch):
    mac, repo, cfg, built = setup(tmp_path, monkeypatch, render="bad_json")
    res = go(mac, repo, cfg)
    assert res.status == "failed"
    report = repo / "build/spring/garden" / f"{built.build_id}.render" / "report.json"
    assert report.read_text() == "{"
    assert str(report) in res.message


def test_mistyped_report_is_kept_as_pulled(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch, status=["ok"])
    monkeypatch.setenv("FAKE_PDF", str(FIXTURES / "pdf" / "spot.pdf"))
    repo, cfg = make_repo(tmp_path, real_seed=True), make_config(tmp_path)
    built = build(repo, cfg, "spring", "garden")
    result = go(mac, repo, cfg)
    remote = mac.path("/Users/j/makeready/jobs/spring/garden/out/report.json").read_text()
    assert result.status == "failed"
    report = repo / "build/spring/garden" / f"{built.build_id}.render" / "report.json"
    assert report.read_text() == remote


def test_wrong_build_records_failure(tmp_path, monkeypatch):
    mac, repo, cfg, built = setup(tmp_path, monkeypatch, render="wrong_build")
    res = go(mac, repo, cfg)
    report = json.loads((repo / "build/spring/garden" / f"{built.build_id}.render" / "report.json").read_text())
    assert report["status"] == "failed"
    assert report["error"] == res.message


def test_timeout_marks_busy(tmp_path, monkeypatch):
    mac, repo, cfg, _ = setup(tmp_path, monkeypatch, render="timeout")
    assert go(mac, repo, cfg).status == "timeout"
    assert is_busy(cfg.cache_dir)
    mac.set(probe="timeout")
    with pytest.raises(MacBusy):
        go(mac, repo, cfg)


def test_dropped_connection_marks_busy(tmp_path, monkeypatch):
    mac, repo, cfg, _ = setup(tmp_path, monkeypatch, render="dropped")
    with pytest.raises(MacUnreachable, match="closed by remote host"):
        go(mac, repo, cfg)
    assert is_busy(cfg.cache_dir)


def test_busy_cleared_by_answer(tmp_path, monkeypatch):
    mac, repo, cfg, _ = setup(tmp_path, monkeypatch)
    mark_busy(cfg.cache_dir)
    assert go(mac, repo, cfg).status == "ok"
    assert not is_busy(cfg.cache_dir)


def test_lock_held(tmp_path, monkeypatch):
    mac, repo, cfg, _ = setup(tmp_path, monkeypatch)
    with render_lock(cfg.cache_dir):
        with pytest.raises(Busy):
            go(mac, repo, cfg)


def test_unreachable(tmp_path, monkeypatch):
    mac, repo, cfg, _ = setup(tmp_path, monkeypatch, unreachable=True)
    with pytest.raises(MacUnreachable):
        go(mac, repo, cfg)


def test_approved_refused(tmp_path, monkeypatch):
    mac, repo, cfg, _ = setup(tmp_path, monkeypatch)
    set_status(repo, "spring", "garden", "approved")
    with pytest.raises(SpecError, match="approved"):
        go(mac, repo, cfg)
    assert not mac.ssh_calls()


def test_no_build(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch)
    repo, cfg = make_repo(tmp_path, real_seed=True), make_config(tmp_path)
    with pytest.raises(SpecError, match="makeready build"):
        go(mac, repo, cfg)


def test_warnings_pass_through(tmp_path, monkeypatch):
    mac, repo, cfg, _ = setup(tmp_path, monkeypatch, status="warnings")
    assert go(mac, repo, cfg).status == "warnings"


def test_colour_oserror_continues_batch(tmp_path, monkeypatch):
    mac, repo, cfg, _ = setup(tmp_path, monkeypatch)
    build(repo, cfg, "spring", "open-house")
    seen = {"n": 0}

    def flaky(pdf, inks, work):
        seen["n"] += 1
        if seen["n"] == 1:
            raise OSError(13, "Permission denied", str(work))
        from makeready.colourcheck import check

        return check(pdf, inks, work)

    monkeypatch.setattr("makeready.render.colour_check", flaky)
    results = render_many(repo, cfg, "spring", ["garden", "open-house"], ssh=mac.ssh, rsync=mac.rsync)
    assert [(item.doc, item.status) for item in results] == [("garden", "failed"), ("open-house", "ok")]
    assert "Permission denied" in results[0].message


def test_pull_oserror_continues_batch(tmp_path, monkeypatch):
    mac, repo, cfg, _ = setup(tmp_path, monkeypatch)
    build(repo, cfg, "spring", "open-house")

    def boom(*_args, **_kwargs):
        raise OSError(13, "Permission denied", str(repo / "build"))

    monkeypatch.setattr("makeready.render.pull", boom)
    results = render_many(repo, cfg, "spring", ["garden", "open-house"], ssh=mac.ssh, rsync=mac.rsync)
    assert [item.status for item in results] == ["failed", "failed"]
    assert "Permission denied" in results[0].message
    assert len([call for call in mac.ssh_calls() if "render.jsx" in call["stdin"]]) == 2


def test_render_lock_oserror_returns_failed(tmp_path, monkeypatch):
    mac, repo, cfg, built = setup(tmp_path, monkeypatch)
    if cfg.cache_dir.exists():
        import shutil

        shutil.rmtree(cfg.cache_dir)
    cfg.cache_dir.write_text("x")
    result = go(mac, repo, cfg)
    assert result.status == "failed"
    assert result.build_id == built.build_id
    assert str(cfg.cache_dir) in result.message


def test_busy_marker_oserror_stays_a_result(tmp_path, monkeypatch):
    mac, repo, cfg, _ = setup(tmp_path, monkeypatch, render="timeout")

    def deny(_cache):
        raise OSError(13, "Permission denied", str(cfg.cache_dir / "mac-busy"))

    monkeypatch.setattr("makeready.render.mark_busy", deny)
    result = go(mac, repo, cfg)
    assert result.status == "timeout"
    assert "Permission denied" in result.message


def test_colour_failure(tmp_path, monkeypatch):
    mac, repo, cfg, b = setup(tmp_path, monkeypatch)
    rgb = tmp_path / "rgb.pdf"
    Image.new("RGB", (60, 40), (200, 80, 30)).save(rgb)
    monkeypatch.setenv("FAKE_PDF", str(rgb))
    res = go(mac, repo, cfg)
    assert res.status == "failed"
    report = json.loads((repo / "build/spring/garden" / f"{b.build_id}.render" / "report.json").read_text())
    assert {f["code"] for f in report["colour"]} >= {"rgb-image", "plates"}
