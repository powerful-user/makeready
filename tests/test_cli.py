from fakes import Mac

from makeready.cli import main


def setup(tmp_path, monkeypatch, **scenario):
    mac = Mac(tmp_path, monkeypatch, **scenario)
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[mac]\nhost = "h"\nhome = "/Users/j"\n'
        f'[assets]\nphotos = "{tmp_path}"\nfonts = "{tmp_path}"\n'
        f'[cache]\ndir = "{tmp_path / "cache"}"\n'
    )
    monkeypatch.setenv("MAKEREADY_CONFIG", str(cfg))
    monkeypatch.setenv("MAKEREADY_SSH", mac.ssh)
    monkeypatch.setenv("MAKEREADY_RSYNC", mac.rsync)
    return mac


def test_version(capsys):
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "makeready 0.1.0"


def test_mac_check(tmp_path, monkeypatch, capsys):
    setup(tmp_path, monkeypatch, version="20.0.1", jobs=["j1\tflyer"])
    assert main(["mac", "check"]) == 0
    out = capsys.readouterr().out
    assert "InDesign 20.0.1" in out
    assert "j1\tflyer" in out


def test_mac_check_missing_ssh(tmp_path, monkeypatch, capsys):
    setup(tmp_path, monkeypatch)
    missing = tmp_path / "no-such-ssh"
    monkeypatch.setenv("MAKEREADY_SSH", str(missing))
    assert main(["mac", "check"]) == 2
    err = capsys.readouterr().err
    assert str(missing) in err
    assert "Traceback" not in err


def test_mac_check_unreachable(tmp_path, monkeypatch, capsys):
    setup(tmp_path, monkeypatch, unreachable=True)
    assert main(["mac", "check"]) == 2
    assert "Mac unreachable" in capsys.readouterr().err


def test_config_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MAKEREADY_CONFIG", str(tmp_path / "missing.toml"))
    assert main(["mac", "check"]) == 2
    assert "missing.toml" in capsys.readouterr().err
