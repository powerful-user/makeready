"""A throwaway copy of tests/fixtures/repo and a matching configuration."""

import shutil
from pathlib import Path

from makeready.config import Config

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
FIXTURES = TESTS / "fixtures"


def make_repo(tmp_path, real_seed=False):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "repo", repo)
    if real_seed:
        shutil.copytree(ROOT / "seed", repo / "seed")
        shutil.copytree(ROOT / "mac", repo / "mac")
    else:
        (repo / "seed").mkdir()
        (repo / "seed" / "reference.idml").write_bytes(b"seed")
        (repo / "mac").mkdir()
        (repo / "mac" / "render.jsx").write_text("// render\n")
    return repo


def make_config(tmp_path, inks=("C", "M", "Y", "K", "Brand Blue"), host="h"):
    return Config(
        figma_token="tok", figma_token_expires=None, mac_host=host, mac_home="/Users/j",
        photos=FIXTURES / "photos", fonts=FIXTURES / "fonts", print_profile="GRACoL2013_CRPC6",
        print_inks=tuple(inks), cache_dir=tmp_path / "cache",
    )


def write_config(tmp_path, inks=("C", "M", "Y", "K", "Brand Blue")):
    """The same configuration as a TOML file, for CLI tests."""
    path = tmp_path / "config.toml"
    ink_list = ", ".join(f'"{i}"' for i in inks)
    path.write_text(
        '[mac]\nhost = "h"\nhome = "/Users/j"\n'
        f'[assets]\nphotos = "{FIXTURES / "photos"}"\nfonts = "{FIXTURES / "fonts"}"\n'
        f'[print]\ninks = [{ink_list}]\n'
        f'[cache]\ndir = "{tmp_path / "cache"}"\n'
    )
    return path
