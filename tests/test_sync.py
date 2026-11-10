import shlex
from pathlib import Path

import pytest
from fakes import Mac

from makeready.mac import MacError
from makeready.sync import pull, push


def test_push(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch)
    src = tmp_path / "src"
    (src / "Document Fonts").mkdir(parents=True)
    (src / "Document Fonts" / "a.otf").write_bytes(b"f")
    push(src, "h", "/Users/j/makeready/jobs/s/d/in", ssh=mac.ssh, rsync=mac.rsync)
    assert mac.path("/Users/j/makeready/jobs/s/d/in/Document Fonts/a.otf").read_bytes() == b"f"
    argv = mac.rsync_calls()[0]
    assert argv[:4] == ["-rt", "--delete", "-e", mac.ssh + " -o BatchMode=yes -o ConnectTimeout=10"]
    assert argv[-2:] == [f"{src}/", "h:/Users/j/makeready/jobs/s/d/in/"]
    assert mac.ssh_calls()[0]["argv"][-1] == "mkdir -p /Users/j/makeready/jobs/s/d/in"


def test_push_deletes_stale(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch)
    stale = mac.path("/r/old.txt")
    stale.parent.mkdir(parents=True)
    stale.write_text("x")
    src = tmp_path / "src"
    src.mkdir()
    (src / "new.txt").write_text("y")
    push(src, "h", "/r", ssh=mac.ssh, rsync=mac.rsync)
    assert not stale.exists()
    assert mac.path("/r/new.txt").exists()


def test_pull(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch)
    report = mac.path("/r/out/report.json")
    report.parent.mkdir(parents=True)
    report.write_text("{}")
    dest = tmp_path / "local" / "render"
    pull("h", "/r/out", dest, ssh=mac.ssh, rsync=mac.rsync)
    assert (dest / "report.json").read_text() == "{}"
    assert mac.rsync_calls()[0][-2:] == ["h:/r/out/", f"{dest}/"]


def test_push_missing_rsync(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch)
    src = tmp_path / "src"
    src.mkdir()
    missing = tmp_path / "no-such-rsync"
    with pytest.raises(MacError) as caught:
        push(src, "h", "/r", ssh=mac.ssh, rsync=str(missing))
    assert str(missing) in str(caught.value)


def test_push_replaces_undecodable_rsync_output(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch)
    bad = tmp_path / "odd-rsync"
    bad.write_text("#!/usr/bin/env python3\nimport sys\nsys.stdout.buffer.write(b'\\xff')\n")
    bad.chmod(0o755)
    src = tmp_path / "src"
    src.mkdir()
    push(src, "h", "/r", ssh=mac.ssh, rsync=str(bad))


def test_push_quotes_ssh_path_with_quote(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch)
    quoted = tmp_path / 'ssh"bin'
    quoted.write_bytes(Path(mac.ssh).read_bytes())
    quoted.chmod(0o755)
    src = tmp_path / "src"
    src.mkdir()
    push(src, "h", "/r", ssh=str(quoted), rsync=mac.rsync)
    assert shlex.split(mac.rsync_calls()[0][3])[0] == str(quoted)


def test_push_quotes_ssh_path_with_space(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch)
    spaced = tmp_path / "dir with space" / "ssh"
    spaced.parent.mkdir()
    spaced.write_bytes(Path(mac.ssh).read_bytes())
    spaced.chmod(0o755)
    src = tmp_path / "src"
    src.mkdir()
    push(src, "h", "/r", ssh=str(spaced), rsync=mac.rsync)
    assert shlex.split(mac.rsync_calls()[0][3])[0] == str(spaced)


def test_only_portable_flags(tmp_path, monkeypatch):
    mac = Mac(tmp_path, monkeypatch)
    src = tmp_path / "s"
    src.mkdir()
    push(src, "h", "/r", ssh=mac.ssh, rsync=mac.rsync)
    assert [a for a in mac.rsync_calls()[0] if a.startswith("-")] == ["-rt", "--delete", "-e"]
