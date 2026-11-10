import re
from datetime import date
from pathlib import Path

import pytest

from makeready import __version__
from makeready.config import ConfigError, load, token_warning

FULL = """
[figma]
token = "figd_abc"
token_expires = 2026-12-31

[mac]
host = "192.0.2.10"
home = "/Users/designer"

[assets]
photos = "~/aid/photos"
fonts = "/srv/fonts"

[print]
profile = "GRACoL2013_CRPC6"
inks = ["C", "M", "Y", "K", "PANTONE 185 C"]

[cache]
dir = "/tmp/aid-cache"
"""

MINIMAL = """
[mac]
host = "192.0.2.10"
home = "/Users/designer"

[assets]
photos = "/p"
fonts = "/f"
"""


def write(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


def test_version():
    assert __version__ == "0.1.0"


def test_load_full(tmp_path):
    cfg = load(write(tmp_path, FULL))
    assert cfg.figma_token == "figd_abc"
    assert cfg.figma_token_expires == date(2026, 12, 31)
    assert cfg.mac_host == "192.0.2.10"
    assert cfg.mac_home == "/Users/designer"
    assert cfg.photos == Path("~/aid/photos").expanduser()
    assert cfg.fonts == Path("/srv/fonts")
    assert cfg.print_profile == "GRACoL2013_CRPC6"
    assert cfg.print_inks == ("C", "M", "Y", "K", "PANTONE 185 C")
    assert cfg.cache_dir == Path("/tmp/aid-cache")


def test_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = load(write(tmp_path, MINIMAL))
    assert cfg.figma_token is None
    assert cfg.figma_token_expires is None
    assert cfg.print_profile == "GRACoL2013_CRPC6"
    assert cfg.print_inks == ("C", "M", "Y", "K")
    assert cfg.cache_dir == tmp_path / ".cache" / "makeready"


def test_env_path(tmp_path, monkeypatch):
    monkeypatch.setenv("MAKEREADY_CONFIG", str(write(tmp_path, MINIMAL)))
    assert load().mac_home == "/Users/designer"


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="nope.toml"):
        load(tmp_path / "nope.toml")


def test_missing_key(tmp_path):
    text = '[mac]\nhost = "h"\n[assets]\nphotos = "/p"\nfonts = "/f"\n'
    with pytest.raises(ConfigError, match=r"mac\.home"):
        load(write(tmp_path, text))


def test_inks_need_process(tmp_path):
    text = MINIMAL + '[print]\ninks = ["K", "PANTONE 185 C"]\n'
    with pytest.raises(ConfigError, match="C, M, Y and K"):
        load(write(tmp_path, text))


def test_token_warning(tmp_path):
    cfg = load(write(tmp_path, FULL))
    assert token_warning(cfg, date(2026, 11, 1)) is None
    assert token_warning(cfg, date(2026, 12, 20)) == "Figma token expires on 2026-12-31 (11 days)"
    assert token_warning(cfg, date(2027, 1, 2)) == "Figma token expired on 2026-12-31"


def test_token_warning_without_expiry(tmp_path):
    cfg = load(write(tmp_path, MINIMAL))
    assert token_warning(cfg, date(2026, 1, 1)) is None


@pytest.mark.parametrize(
    "line, key",
    [
        ('[assets]\nphotos = 1\nfonts = "/f"\n', "assets.photos"),
        ('[assets]\nphotos = "/p"\nfonts = ["/f"]\n', "assets.fonts"),
        ('[figma]\ntoken_expires = "2026-12-31"\n', "figma.token_expires"),
        ("[figma]\ntoken_expires = 2026-12-31T10:00:00\n", "figma.token_expires"),
        ("[figma]\ntoken = 5\n", "figma.token"),
        ("[print]\nprofile = 1\n", "print.profile"),
        ("[cache]\ndir = false\n", "cache.dir"),
        ('[print]\ninks = ["C", "M", "Y", "K", 5]\n', "print.inks"),
        ('[mac]\nhost = 1\nhome = "/Users/j"\n', "mac.host"),
        ('[mac]\nhost = "h"\nhome = 2\n', "mac.home"),
    ],
)
def test_wrong_type(tmp_path, line, key):
    base = "" if line.startswith("[mac]") else '[mac]\nhost = "h"\nhome = "/Users/j"\n'
    if not line.startswith("[assets]"):
        base += '[assets]\nphotos = "/p"\nfonts = "/f"\n'
    path = write(tmp_path, base + line)
    with pytest.raises(ConfigError, match=re.escape(f"{path}: {key} must be")):
        load(path)


def test_malformed_toml(tmp_path):
    path = write(tmp_path, "[mac\nhost = 1\n")
    with pytest.raises(ConfigError, match=re.escape(str(path))):
        load(path)


@pytest.mark.parametrize("section", ["mac", "assets", "figma", "print", "cache"])
def test_section_must_be_table(tmp_path, section):
    text = MINIMAL.replace("[mac]", "[mac_]").replace("[assets]", "[assets_]")
    text = f"{section} = 3\n" + text
    text = text.replace("[mac_]", "[mac]" if section != "mac" else "[other_mac]")
    text = text.replace("[assets_]", "[assets]" if section != "assets" else "[other_assets]")
    path = write(tmp_path, text)
    with pytest.raises(ConfigError, match=re.escape(f"{path}: [{section}] must be a table")):
        load(path)


def test_config_not_utf8(tmp_path):
    path = tmp_path / "config.toml"
    path.write_bytes(b'[mac]\nhost = "\xff"\n')
    with pytest.raises(ConfigError, match=re.escape(str(path))):
        load(path)
