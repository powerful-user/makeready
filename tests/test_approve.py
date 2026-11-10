import json
from datetime import datetime

import pytest
from fakes import Mac
from fixture_repo import FIXTURES, make_config, make_repo, write_config

from makeready.approve import ApproveError, approve, mac_reset, open_pdf
from makeready.build import build
from makeready.cli import main
from makeready.mac import MacError
from makeready.render import render
from makeready.spec import resolve
from makeready.state import Busy, is_busy, mark_busy, render_lock


def rendered(tmp_path, monkeypatch, **scenario):
    mac = Mac(tmp_path, monkeypatch, **scenario)
    monkeypatch.setenv("FAKE_PDF", str(FIXTURES / "pdf" / "spot.pdf"))
    repo, cfg = make_repo(tmp_path, real_seed=True), make_config(tmp_path)
    b = build(repo, cfg, "spring", "garden")
    render(repo, cfg, "spring", "garden", ssh=mac.ssh, rsync=mac.rsync)
    return mac, repo, cfg, b


def test_approve_ok(tmp_path, monkeypatch):
    mac, repo, cfg, b = rendered(tmp_path, monkeypatch)
    approve(repo, cfg, "spring", "garden", ssh=mac.ssh, rsync=mac.rsync)
    assert resolve(repo, "spring", "garden").status == "approved"
    report = repo / "build/spring/garden" / f"{b.build_id}.render" / "package-report.json"
    assert json.loads(report.read_text())["status"] == "ok"
    calls = mac.ssh_calls()
    package = [c for c in calls if "package.jsx" in c["stdin"]]
    assert len(package) == 1
    assert "/Users/j/makeready/handoff/spring/garden" in package[0]["stdin"]
    assert calls[-1]["argv"][-1] == "cat /Users/j/makeready/handoff/spring/garden/package-report.json"


def test_approve_refuses_when_mac_holds_another_build(tmp_path, monkeypatch):
    mac, repo, cfg, latest = rendered(tmp_path, monkeypatch)
    spec = repo / "series/spring/garden.yaml"
    original = spec.read_text()
    spec.write_text("overrides:\n  headline: {tracking: -5}\n")
    other = build(repo, cfg, "spring", "garden")
    assert other.build_id != latest.build_id
    spec.write_text(original)
    rebuilt = build(repo, cfg, "spring", "garden")
    assert rebuilt.build_id == latest.build_id
    render(repo, cfg, "spring", "garden", build_id=other.build_id, ssh=mac.ssh, rsync=mac.rsync)
    with pytest.raises(ApproveError, match="the Mac holds a different render"):
        approve(repo, cfg, "spring", "garden", ssh=mac.ssh, rsync=mac.rsync)
    assert resolve(repo, "spring", "garden").status == "draft"
    assert not [c for c in mac.ssh_calls() if "package.jsx" in c["stdin"]]


def test_approve_refuses_when_mac_holds_a_later_render_of_the_same_build(tmp_path, monkeypatch):
    mac, repo, cfg, built = rendered(tmp_path, monkeypatch)
    first = json.loads(_render_report(repo, built.build_id).read_text())["job"]

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2099, 1, 1, tzinfo=tz)

    def lost(*_args, **_kwargs):
        raise MacError("rsync: connection dropped")

    monkeypatch.setattr("makeready.render.datetime", Later)
    monkeypatch.setattr("makeready.render.pull", lost)
    with pytest.raises(MacError):
        render(repo, cfg, "spring", "garden", ssh=mac.ssh, rsync=mac.rsync)
    remote = "/Users/j/makeready/jobs/spring/garden/out/report.json"
    assert json.loads(mac.path(remote).read_text())["job"] != first
    assert json.loads(_render_report(repo, built.build_id).read_text())["job"] == first
    with pytest.raises(ApproveError, match="the Mac holds a different render"):
        approve(repo, cfg, "spring", "garden", ssh=mac.ssh, rsync=mac.rsync)
    assert resolve(repo, "spring", "garden").status == "draft"
    assert not [c for c in mac.ssh_calls() if "package.jsx" in c["stdin"]]


def test_approve_stale_inputs(tmp_path, monkeypatch):
    mac, repo, cfg, _ = rendered(tmp_path, monkeypatch)
    (repo / "series/spring/garden.yaml").write_text("overrides:\n  headline: {tracking: -5}\n")
    with pytest.raises(ApproveError, match="inputs changed"):
        approve(repo, cfg, "spring", "garden", ssh=mac.ssh, rsync=mac.rsync)
    assert resolve(repo, "spring", "garden").status == "draft"
    assert not [c for c in mac.ssh_calls() if "package.jsx" in c["stdin"]]


def _render_report(repo, build_id):
    return repo / "build/spring/garden" / f"{build_id}.render" / "report.json"


def test_colour_tool_failure_blocks_approve(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_PDF", str(FIXTURES / "pdf" / "spot.pdf"))
    repo, cfg = make_repo(tmp_path, real_seed=True), make_config(tmp_path)
    built = build(repo, cfg, "spring", "garden")

    def boom(*_args, **_kwargs):
        raise RuntimeError("gs failed: broken")

    monkeypatch.setattr("makeready.render.colour_check", boom)
    result = render(repo, cfg, "spring", "garden", ssh=mac.ssh, rsync=mac.rsync)
    assert result.status == "failed"
    report = json.loads(_render_report(repo, built.build_id).read_text())
    assert report["status"] == "failed"
    assert report["error"] == "gs failed: broken"
    with pytest.raises(ApproveError):
        approve(repo, cfg, "spring", "garden", ssh=mac.ssh, rsync=mac.rsync)
    assert resolve(repo, "spring", "garden").status == "draft"
    assert not [call for call in mac.ssh_calls() if "package.jsx" in call["stdin"]]


def test_missing_pdf_blocks_approve(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch, render="no_pdf")
    monkeypatch.setenv("FAKE_PDF", str(FIXTURES / "pdf" / "spot.pdf"))
    repo, cfg = make_repo(tmp_path, real_seed=True), make_config(tmp_path)
    built = build(repo, cfg, "spring", "garden")
    result = render(repo, cfg, "spring", "garden", ssh=mac.ssh, rsync=mac.rsync)
    assert result.status == "failed"
    report = json.loads(_render_report(repo, built.build_id).read_text())
    assert report["status"] == "failed"
    assert report["error"] == "render failed: no garden.pdf"
    with pytest.raises(ApproveError):
        approve(repo, cfg, "spring", "garden", ssh=mac.ssh, rsync=mac.rsync)
    assert resolve(repo, "spring", "garden").status == "draft"
    assert not [call for call in mac.ssh_calls() if "package.jsx" in call["stdin"]]


def test_approve_refuses_when_pdf_is_gone(tmp_path, monkeypatch):
    mac, repo, cfg, built = rendered(tmp_path, monkeypatch)
    pdf = _render_report(repo, built.build_id).parent / "garden.pdf"
    pdf.unlink()
    with pytest.raises(ApproveError):
        approve(repo, cfg, "spring", "garden", ssh=mac.ssh, rsync=mac.rsync)
    assert resolve(repo, "spring", "garden").status == "draft"
    assert not [call for call in mac.ssh_calls() if "package.jsx" in call["stdin"]]


def test_report_write_failure_blocks_approve(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_PDF", str(FIXTURES / "pdf" / "spot.pdf"))
    repo, cfg = make_repo(tmp_path, real_seed=True), make_config(tmp_path)
    built = build(repo, cfg, "spring", "garden")

    def fail_write(path, _report):
        return f"{path}: read-only"

    monkeypatch.setattr("makeready.render._write_report", fail_write)
    result = render(repo, cfg, "spring", "garden", ssh=mac.ssh, rsync=mac.rsync)
    assert result.status == "failed"
    report = json.loads(_render_report(repo, built.build_id).read_text())
    assert "colour" not in report
    with pytest.raises(ApproveError):
        approve(repo, cfg, "spring", "garden", ssh=mac.ssh, rsync=mac.rsync)
    assert resolve(repo, "spring", "garden").status == "draft"
    assert not [call for call in mac.ssh_calls() if "package.jsx" in call["stdin"]]


def test_approve_rejects_mistyped_report_field(tmp_path, monkeypatch):
    mac, repo, cfg, built = rendered(tmp_path, monkeypatch)
    path = _render_report(repo, built.build_id)
    data = json.loads(path.read_text())
    data["inks"] = [1]
    path.write_text(json.dumps(data))
    with pytest.raises(ApproveError, match="inks") as caught:
        approve(repo, cfg, "spring", "garden", ssh=mac.ssh, rsync=mac.rsync)
    assert str(path) in str(caught.value)
    assert resolve(repo, "spring", "garden").status == "draft"
    assert not [call for call in mac.ssh_calls() if "package.jsx" in call["stdin"]]


def test_approve_rejects_mistyped_mac_report(tmp_path, monkeypatch):
    mac, repo, cfg, _built = rendered(tmp_path, monkeypatch)
    remote = "/Users/j/makeready/jobs/spring/garden/out/report.json"
    data = json.loads(mac.path(remote).read_text())
    data["status"] = ["ok"]
    mac.path(remote).write_text(json.dumps(data))
    with pytest.raises(ApproveError, match="status") as caught:
        approve(repo, cfg, "spring", "garden", ssh=mac.ssh, rsync=mac.rsync)
    assert remote in str(caught.value)
    assert resolve(repo, "spring", "garden").status == "draft"
    assert not [call for call in mac.ssh_calls() if "package.jsx" in call["stdin"]]


def test_approve_refuses_a_kept_bad_report(tmp_path, monkeypatch):
    mac, repo, cfg, built = rendered(tmp_path, monkeypatch, render="bad_json")
    with pytest.raises(ApproveError, match="report.json"):
        approve(repo, cfg, "spring", "garden", ssh=mac.ssh, rsync=mac.rsync)
    assert _render_report(repo, built.build_id).read_text() == "{"
    assert resolve(repo, "spring", "garden").status == "draft"
    assert not [c for c in mac.ssh_calls() if "package.jsx" in c["stdin"]]


def test_approve_failed_render(tmp_path, monkeypatch):
    mac, repo, cfg, _ = rendered(tmp_path, monkeypatch, render="no_report")
    with pytest.raises(ApproveError, match="did not succeed"):
        approve(repo, cfg, "spring", "garden", ssh=mac.ssh, rsync=mac.rsync)


def test_approve_package_failure(tmp_path, monkeypatch):
    mac, repo, cfg, _ = rendered(tmp_path, monkeypatch, package="missing_font")
    with pytest.raises(ApproveError, match="Nimbus Sans"):
        approve(repo, cfg, "spring", "garden", ssh=mac.ssh, rsync=mac.rsync)
    assert resolve(repo, "spring", "garden").status == "draft"


def test_approve_takes_lock(tmp_path, monkeypatch):
    mac, repo, cfg, _ = rendered(tmp_path, monkeypatch)
    with render_lock(cfg.cache_dir):
        with pytest.raises(Busy):
            approve(repo, cfg, "spring", "garden", ssh=mac.ssh, rsync=mac.rsync)


def test_approve_cache_oserror(tmp_path, monkeypatch):
    mac, repo, cfg, _ = rendered(tmp_path, monkeypatch)
    import shutil

    shutil.rmtree(cfg.cache_dir)
    cfg.cache_dir.write_text("x")
    with pytest.raises(ApproveError, match="cache"):
        approve(repo, cfg, "spring", "garden", ssh=mac.ssh, rsync=mac.rsync)
    assert not [call for call in mac.ssh_calls() if "package.jsx" in call["stdin"]]


def test_mac_reset_unlink_oserror(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch)
    cfg = make_config(tmp_path)
    mark_busy(cfg.cache_dir)
    cfg.cache_dir.chmod(0o555)
    try:
        with pytest.raises(ApproveError, match="cache"):
            mac_reset(cfg, ssh=mac.ssh)
    finally:
        cfg.cache_dir.chmod(0o755)


def test_cli_proof_io_error(tmp_path, monkeypatch, capsys):
    _mac, repo, _cfg, built = rendered(tmp_path, monkeypatch)
    folder = repo / "build/spring/garden" / f"{built.build_id}.render" / "proof"
    folder.write_text("nope")
    assert main(["--repo", str(repo), "proof", "spring/garden"]) == 1
    assert str(folder) in capsys.readouterr().err


def test_mac_reset(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch)
    cfg = make_config(tmp_path)
    mark_busy(cfg.cache_dir)
    mac_reset(cfg, ssh=mac.ssh)
    call = mac.ssh_calls()[-1]
    assert call["argv"][-1] == "sh -s"
    assert "InDesign Recovery" in call["stdin"]
    assert "pkill" in call["stdin"]
    assert not is_busy(cfg.cache_dir)


def test_open_pdf(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch)
    open_pdf(make_config(tmp_path), "spring", "garden", ssh=mac.ssh)
    assert mac.ssh_calls()[-1]["argv"][-1] == "open /Users/j/makeready/jobs/spring/garden/out/garden.pdf"


def test_cli_flow(tmp_path, monkeypatch, capsys):
    mac = Mac(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_PDF", str(FIXTURES / "pdf" / "spot.pdf"))
    monkeypatch.setenv("MAKEREADY_CONFIG", str(write_config(tmp_path)))
    monkeypatch.setenv("MAKEREADY_SSH", mac.ssh)
    monkeypatch.setenv("MAKEREADY_RSYNC", mac.rsync)
    repo = make_repo(tmp_path, real_seed=True)
    r = ["--repo", str(repo)]
    assert main(r + ["build", "spring/garden"]) == 0
    assert main(r + ["render", "spring/garden"]) == 0
    assert main(r + ["proof", "spring/garden"]) == 0
    assert main(r + ["open", "spring/garden"]) == 0
    assert main(r + ["approve", "spring/garden"]) == 0
    assert main(r + ["mac-reset"]) == 0
    out = capsys.readouterr().out
    assert "proof-1.png" in out
    assert "approved" in out


def test_cli_render_failure_exit(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch, render="no_report")
    monkeypatch.setenv("FAKE_PDF", str(FIXTURES / "pdf" / "spot.pdf"))
    monkeypatch.setenv("MAKEREADY_CONFIG", str(write_config(tmp_path)))
    monkeypatch.setenv("MAKEREADY_SSH", mac.ssh)
    monkeypatch.setenv("MAKEREADY_RSYNC", mac.rsync)
    repo = make_repo(tmp_path, real_seed=True)
    assert main(["--repo", str(repo), "build", "spring/garden"]) == 0
    assert main(["--repo", str(repo), "render", "spring/garden"]) == 1


def test_cli_render_series_one_lock(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_PDF", str(FIXTURES / "pdf" / "spot.pdf"))
    monkeypatch.setenv("MAKEREADY_CONFIG", str(write_config(tmp_path)))
    monkeypatch.setenv("MAKEREADY_SSH", mac.ssh)
    monkeypatch.setenv("MAKEREADY_RSYNC", mac.rsync)
    repo = make_repo(tmp_path, real_seed=True)
    assert main(["--repo", str(repo), "build", "spring"]) == 0
    assert main(["--repo", str(repo), "render", "spring"]) == 0
    calls = mac.ssh_calls()
    assert len([c for c in calls if "aid-probe" in c["stdin"]]) == 1
    assert len([c for c in calls if "render.jsx" in c["stdin"]]) == 2


def test_cli_render_series_stops_after_timeout(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch, render="timeout")
    monkeypatch.setenv("FAKE_PDF", str(FIXTURES / "pdf" / "spot.pdf"))
    monkeypatch.setenv("MAKEREADY_CONFIG", str(write_config(tmp_path)))
    monkeypatch.setenv("MAKEREADY_SSH", mac.ssh)
    monkeypatch.setenv("MAKEREADY_RSYNC", mac.rsync)
    repo = make_repo(tmp_path, real_seed=True)
    assert main(["--repo", str(repo), "build", "spring"]) == 0
    assert main(["--repo", str(repo), "render", "spring"]) == 1
    assert len([c for c in mac.ssh_calls() if "render.jsx" in c["stdin"]]) == 1
    pushed = [c[-1] for c in mac.rsync_calls() if "/jobs/" in c[-1]]
    assert len(pushed) == 1
