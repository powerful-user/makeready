"""`makeready init` and the project-level settings file it writes."""

import shutil
import subprocess
import tomllib
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from fixture_repo import ROOT, STARTER
from makeready.cli import main
from makeready.config import ConfigError, load, require_mac
from makeready.idml.validate import validate


@pytest.fixture
def no_user_config(tmp_path, monkeypatch):
    """Nothing but the project can supply settings."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("MAKEREADY_CONFIG", raising=False)


def test_init_then_build_needs_no_mac_and_no_user_config(tmp_path, no_user_config, capsys):
    project = tmp_path / "job"
    assert main(["init", str(project)]) == 0
    assert main(["--repo", str(project), "build", "example"]) == 0
    out = capsys.readouterr().out
    assert "warning" not in out
    idml = project / "build" / "example" / "open-house" / "latest" / "open-house.idml"
    assert idml.is_file()
    assert validate(idml, project / "seed" / "schema") == []


def test_init_tells_the_user_what_to_run_next(tmp_path, no_user_config, capsys):
    project = tmp_path / "job"
    main(["init", str(project)])
    assert f"makeready --repo {project} build example" in capsys.readouterr().out


def test_init_fills_an_existing_empty_folder(tmp_path, no_user_config):
    project = tmp_path / "job"
    project.mkdir()
    assert main(["init", str(project)]) == 0
    assert (project / "makeready.toml").is_file()


def test_init_refuses_a_folder_with_files_and_leaves_them(tmp_path, no_user_config, capsys):
    project = tmp_path / "job"
    project.mkdir()
    (project / "notes.txt").write_text("mine")
    assert main(["init", str(project)]) == 2
    assert "not empty" in capsys.readouterr().err
    assert [path.name for path in project.iterdir()] == ["notes.txt"]


def test_init_warns_when_jing_is_missing(tmp_path, no_user_config, monkeypatch, capsys):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert main(["init", str(tmp_path / "job")]) == 0
    assert "brew install jing-trang" in capsys.readouterr().err


def test_project_settings_resolve_paths_beside_the_file(tmp_path, no_user_config):
    project = tmp_path / "job"
    main(["init", str(project)])
    cfg = load(repo=project)
    assert cfg.photos == project.resolve() / "photos"
    assert cfg.fonts == project.resolve() / "fonts"


def test_makeready_config_beats_project_settings(tmp_path, no_user_config, monkeypatch):
    project = tmp_path / "job"
    main(["init", str(project)])
    explicit = tmp_path / "explicit.toml"
    explicit.write_text('[mac]\nhost = "h"\nhome = "/Users/j"\n[assets]\nphotos = "/p"\nfonts = "/f"\n')
    monkeypatch.setenv("MAKEREADY_CONFIG", str(explicit))
    assert load(repo=project).mac_host == "h"


def test_user_config_is_used_when_a_project_has_no_settings(tmp_path, no_user_config):
    folder = Path.home() / ".config" / "makeready"
    folder.mkdir(parents=True)
    (folder / "config.toml").write_text(
        '[mac]\nhost = "h"\nhome = "/Users/j"\n[assets]\nphotos = "/p"\nfonts = "/f"\n'
    )
    assert load(repo=tmp_path).mac_host == "h"


def test_require_mac_names_the_setting():
    cfg = type("Cfg", (), {"mac_host": ""})()
    with pytest.raises(ConfigError, match=r"\[mac\] host"):
        require_mac(cfg)


@pytest.mark.parametrize(
    "command",
    [["mac", "check"], ["mac", "bootstrap"], ["render", "example/garden"], ["approve", "example/garden"],
     ["open", "example/garden"], ["mac-reset"]],
)
def test_mac_commands_stop_without_a_mac(tmp_path, no_user_config, capsys, command):
    project = tmp_path / "job"
    main(["init", str(project)])
    capsys.readouterr()
    assert main(["--repo", str(project), *command]) == 2
    assert "[mac] host" in capsys.readouterr().err


def test_starter_photo_is_print_resolution():
    with Image.open(STARTER / "photos" / "hero.jpg") as image:
        assert image.info["dpi"][0] >= 299


def test_starter_fonts_carry_their_licence():
    assert "SIL OPEN FONT LICENSE" in (STARTER / "fonts" / "OFL.txt").read_text()


def test_package_metadata_is_ready_to_publish():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["readme"] == "README.md"
    assert project["license"] == "MIT AND OFL-1.1"
    assert set(project["license-files"]) == {"LICENSE", "src/makeready/starter/fonts/OFL.txt"}
    assert "Repository" in project["urls"]
    assert project["classifiers"]
    assert "Figma" not in project["description"]


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv")
def test_wheel_ships_the_starter_project(tmp_path):
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)], cwd=ROOT, check=True, capture_output=True
    )
    [wheel] = tmp_path.glob("*.whl")
    names = zipfile.ZipFile(wheel).namelist()
    prefix = "makeready/starter/"
    for part in (
        "seed/reference.idml", "seed/schema/designmap.rnc", "seed/schema-map.json", "mac/render.jsx",
        "makeready.toml", "brand/swatches.yaml", "brand/font-map.yaml", "templates/flyer/template.json",
        "series/example/series.yaml", "photos/hero.jpg", "fonts/LiberationSans-Regular.ttf", "fonts/OFL.txt",
        ".gitignore",
    ):
        assert prefix + part in names, part
