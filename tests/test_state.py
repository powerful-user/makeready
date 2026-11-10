import pytest
from fakes import Mac

from makeready.mac import PROBE_JS, MacBusy, MacError, MacNotResponding, Probe, probe
from makeready.state import Busy, check_mac, clear_busy, is_busy, mark_busy, render_lock


def test_probe_js_marker():
    assert "aid-probe" in PROBE_JS
    assert "aid.job" in PROBE_JS


def test_probe_parses(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch, version="20.0.1", jobs=["abc-1\tflyer.indd"])
    assert probe("h", ssh=mac.ssh) == Probe(version="20.0.1", jobs=["abc-1\tflyer.indd"])
    assert "with timeout of 90 seconds" in mac.ssh_calls()[0]["stdin"]


def test_probe_rejects_empty_version(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch, probe="empty")
    with pytest.raises(MacError, match="InDesign probe returned no version"):
        probe("h", ssh=mac.ssh)


def test_check_mac_keeps_busy_when_probe_fails(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch, probe="empty")
    cache = tmp_path / "cache"
    mark_busy(cache)
    with pytest.raises(MacError, match="InDesign probe returned no version"):
        check_mac("h", cache, ssh=mac.ssh)
    assert is_busy(cache)


def test_lock_is_exclusive(tmp_path):
    with render_lock(tmp_path):
        with pytest.raises(Busy, match="render already running"):
            with render_lock(tmp_path):
                pass
    with render_lock(tmp_path):
        pass


def test_busy_marker(tmp_path):
    assert not is_busy(tmp_path)
    mark_busy(tmp_path)
    assert is_busy(tmp_path)
    clear_busy(tmp_path)
    assert not is_busy(tmp_path)
    clear_busy(tmp_path)


def test_check_mac_clears_busy_on_answer(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    mark_busy(cache)
    assert check_mac("h", cache, ssh=mac.ssh).version == "20.0.1"
    assert not is_busy(cache)


def test_check_mac_busy_and_silent(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch, probe="timeout")
    cache = tmp_path / "cache"
    mark_busy(cache)
    with pytest.raises(MacBusy, match="makeready mac-reset"):
        check_mac("h", cache, ssh=mac.ssh)
    assert is_busy(cache)


def test_check_mac_silent_not_busy(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch, probe="timeout")
    with pytest.raises(MacNotResponding, match="check the Mac screen"):
        check_mac("h", tmp_path / "cache", ssh=mac.ssh)
