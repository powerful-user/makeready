"""Load the makeready configuration file."""

import os
import tomllib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

_PROCESS = ("C", "M", "Y", "K")
PROJECT_FILE = "makeready.toml"


class ConfigError(Exception):
    """A configuration file is missing or invalid."""


@dataclass(frozen=True)
class Config:
    figma_token: str | None
    figma_token_expires: date | None
    mac_host: str
    mac_home: str
    photos: Path
    fonts: Path
    print_profile: str
    print_inks: tuple[str, ...]
    cache_dir: Path


def _table(data: dict, name: str) -> dict:
    return data.get(name, {})


def _typed(path: Path, data: dict, section: str, key: str, kind: type, what: str, default=None):
    value = _table(data, section).get(key, default)
    if value is not None and not isinstance(value, kind):
        raise ConfigError(f"{path}: {section}.{key} must be {what}")
    return value


def load(path: Path | None = None, repo: Path | None = None) -> Config:
    """Read settings from `path`, $MAKEREADY_CONFIG, the project's file or the user's, first found."""
    base = None
    if path is None:
        env = os.environ.get("MAKEREADY_CONFIG")
        project = Path(repo) / PROJECT_FILE if repo is not None else None
        if env:
            path = Path(env)
        elif project is not None and project.is_file():
            path = project
            base = project.parent.resolve()
        else:
            path = Path("~/.config/makeready/config.toml")
    path = Path(path).expanduser()
    if not path.is_file():
        raise ConfigError(f"{path}: file not found")
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise ConfigError(f"{path}: {error}") from error
    for section in ("mac", "assets", "figma", "print", "cache"):
        if not isinstance(data.get(section, {}), dict):
            raise ConfigError(f"{path}: [{section}] must be a table")

    for section, key in (
        ("mac", "host"),
        ("mac", "home"),
        ("assets", "photos"),
        ("assets", "fonts"),
    ):
        if key not in _table(data, section):
            raise ConfigError(f"{path}: missing {section}.{key}")

    expires = _typed(path, data, "figma", "token_expires", date, "a date such as 2026-12-31")
    if isinstance(expires, datetime):
        raise ConfigError(f"{path}: figma.token_expires must be a date such as 2026-12-31")
    inks = _typed(path, data, "print", "inks", list, "a list of ink names", list(_PROCESS))
    if any(not isinstance(ink, str) for ink in inks):
        raise ConfigError(f"{path}: print.inks must be a list of ink names")
    if any(ink not in inks for ink in _PROCESS):
        raise ConfigError(f"{path}: print.inks must include C, M, Y and K")

    return Config(
        figma_token=_typed(path, data, "figma", "token", str, "text"),
        figma_token_expires=expires,
        mac_host=_typed(path, data, "mac", "host", str, "text"),
        mac_home=str(Path(_typed(path, data, "mac", "home", str, "text")).expanduser()),
        photos=_folder(_typed(path, data, "assets", "photos", str, "text"), base),
        fonts=_folder(_typed(path, data, "assets", "fonts", str, "text"), base),
        print_profile=_typed(path, data, "print", "profile", str, "text", "GRACoL2013_CRPC6"),
        print_inks=tuple(inks),
        cache_dir=Path(_typed(path, data, "cache", "dir", str, "text", "~/.cache/makeready")).expanduser(),
    )


def _folder(value: str, base: Path | None) -> Path:
    folder = Path(value).expanduser()
    return base / folder if base is not None else folder


def require_mac(cfg: Config) -> None:
    """Stop a command that needs the Mac when none is configured."""
    if not cfg.mac_host:
        raise ConfigError(f"no Mac configured: set [mac] host and home in {PROJECT_FILE}")


def token_warning(cfg: Config, today: date) -> str | None:
    expires = cfg.figma_token_expires
    if expires is None:
        return None
    days = (expires - today).days
    if days < 0:
        return f"Figma token expired on {expires:%Y-%m-%d}"
    if days <= 14:
        return f"Figma token expires on {expires:%Y-%m-%d} ({days} days)"
    return None
