import pytest
from fakes import Mac

from makeready.mac import (
    MacError,
    MacNotResponding,
    MacUnreachable,
    as_string,
    file_call,
    js_call,
    run_applescript,
    run_script,
    run_shell,
)


def test_as_string():
    assert as_string('say "hi" \\ bye') == '"say \\"hi\\" \\\\ bye"'
    assert as_string("a\nb\tc") == '"a\\nb\\tc"'


def test_js_call():
    s = js_call('var a = "x";', ["/Users/j/Document Fonts"], 30)
    assert s.splitlines()[0] == "with timeout of 30 seconds"
    assert 'tell application id "com.adobe.InDesign"' in s
    assert 'do script "var a = \\"x\\";" language javascript with arguments {"/Users/j/Document Fonts"}' in s
    assert s.rstrip().endswith("end timeout")


def test_file_call():
    s = file_call("/Users/j/makeready/scripts/render.jsx", ["a", "b"], 600)
    assert "with timeout of 600 seconds" in s
    assert (
        'do script (POSIX file "/Users/j/makeready/scripts/render.jsx") '
        'language javascript with arguments {"a", "b"}'
    ) in s


def test_run_applescript_sends_stdin(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch, version="20.0.1")
    out = run_applescript("h", js_call("// aid-probe", [], 30), 30, ssh=mac.ssh)
    assert out == "20.0.1"
    call = mac.ssh_calls()[0]
    assert call["argv"][-2:] == ["h", "osascript -"]
    assert call["argv"][:4] == ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    assert "aid-probe" in call["stdin"]


def test_run_applescript_prefix(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch)
    run_applescript("h", js_call("// aid-probe", [], 30), 30, ssh=mac.ssh, prefix=["caffeinate", "-i"])
    assert mac.ssh_calls()[0]["argv"][-1] == "caffeinate -i osascript -"


def test_run_applescript_quotes_prefix_with_space(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch)
    with pytest.raises(MacError):
        run_applescript("h", js_call("// aid-probe", [], 30), 30, ssh=mac.ssh, prefix=["my tool"])
    assert mac.ssh_calls()[0]["argv"][-1] == "'my tool' osascript -"


def test_unreachable(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch, unreachable=True)
    with pytest.raises(MacUnreachable, match="Connection refused"):
        run_applescript("h", "x", 30, ssh=mac.ssh)


def test_apple_event_timeout(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch, probe="timeout")
    with pytest.raises(MacNotResponding):
        run_applescript("h", js_call("// aid-probe", [], 30), 30, ssh=mac.ssh)


def test_other_failure(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch)
    with pytest.raises(MacError, match="unexpected script"):
        run_applescript("h", js_call("nothing", [], 30), 30, ssh=mac.ssh)


def test_subprocess_timeout(tmp_path):
    slow = tmp_path / "slow-ssh"
    slow.write_text("#!/bin/sh\nsleep 5\n")
    slow.chmod(0o755)
    with pytest.raises(MacNotResponding):
        run_applescript("h", "x", 0, ssh=str(slow), grace_s=0.5)


def test_run_shell_missing_program(tmp_path):
    missing = tmp_path / "no-such-ssh"
    with pytest.raises(MacError) as caught:
        run_shell("h", ["true"], ssh=str(missing))
    assert str(missing) in str(caught.value)


def test_run_shell_replaces_undecodable_output(tmp_path):
    prog = tmp_path / "odd-ssh"
    prog.write_text("#!/usr/bin/env python3\nimport sys\nsys.stdout.buffer.write(b'\\xff\\n')\n")
    prog.chmod(0o755)
    assert "\ufffd" in run_shell("h", ["true"], ssh=str(prog))


def test_run_shell_quotes(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch)
    run_shell("h", ["mkdir", "-p", "/Users/j/a b/Document Fonts"], ssh=mac.ssh)
    assert mac.path("/Users/j/a b/Document Fonts").is_dir()
    assert mac.ssh_calls()[0]["argv"][-1] == "mkdir -p '/Users/j/a b/Document Fonts'"


def test_run_script(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch)
    run_script("h", "echo hi\n", ssh=mac.ssh)
    call = mac.ssh_calls()[0]
    assert call["argv"][-1] == "sh -s"
    assert call["stdin"] == "echo hi\n"
