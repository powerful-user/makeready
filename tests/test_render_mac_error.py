import json

from fakes import Mac
from fixture_repo import FIXTURES, make_config, make_repo

from makeready.build import build
from makeready.render import render


def test_mac_failure_keeps_indesign_error(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch, render="open_fails")
    monkeypatch.setenv("FAKE_PDF", str(FIXTURES / "pdf" / "spot.pdf"))
    repo, cfg = make_repo(tmp_path, real_seed=True), make_config(tmp_path)
    built = build(repo, cfg, "spring", "garden")
    result = render(repo, cfg, "spring", "garden", ssh=mac.ssh, rsync=mac.rsync)
    assert result.status == "failed"
    assert result.message == "Cannot open the file “garden.idml”."
    remote = mac.path("/Users/j/makeready/jobs/spring/garden/out/report.json").read_text()
    local = repo / "build/spring/garden" / f"{built.build_id}.render" / "report.json"
    assert local.read_text() == remote


def test_mac_failure_without_error_text(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch, status="failed")
    monkeypatch.setenv("FAKE_PDF", str(FIXTURES / "pdf" / "spot.pdf"))
    repo, cfg = make_repo(tmp_path, real_seed=True), make_config(tmp_path)
    built = build(repo, cfg, "spring", "garden")

    def no_colour_check(*_args, **_kwargs):
        raise AssertionError("a failed report must not be colour-checked")

    monkeypatch.setattr("makeready.render.colour_check", no_colour_check)
    result = render(repo, cfg, "spring", "garden", ssh=mac.ssh, rsync=mac.rsync)
    assert result.status == "failed"
    assert result.message == "render failed on the Mac"
    remote = mac.path("/Users/j/makeready/jobs/spring/garden/out/report.json").read_text()
    local = repo / "build/spring/garden" / f"{built.build_id}.render" / "report.json"
    assert local.read_text() == remote


def test_failed_report_for_another_build_is_the_wrong_build(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch, render="wrong_build", status="failed")
    monkeypatch.setenv("FAKE_PDF", str(FIXTURES / "pdf" / "spot.pdf"))
    repo, cfg = make_repo(tmp_path, real_seed=True), make_config(tmp_path)
    built = build(repo, cfg, "spring", "garden")
    result = render(repo, cfg, "spring", "garden", ssh=mac.ssh, rsync=mac.rsync)
    assert result.status == "failed"
    assert result.message == f"report is for build 0000000000000000, not {built.build_id}"
    local = repo / "build/spring/garden" / f"{built.build_id}.render" / "report.json"
    assert json.loads(local.read_text())["error"] == result.message
