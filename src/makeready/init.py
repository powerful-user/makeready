"""Create a new project from the starter files shipped with the package."""

import shutil
from pathlib import Path

STARTER = Path(__file__).resolve().parent / "starter"


class InitError(Exception):
    """The target folder cannot take a new project."""


def init(target: Path) -> None:
    """Copy the starter project into `target`, which must be absent or empty."""
    target = Path(target)
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise InitError(f"{target}: not empty; choose a new folder")
    try:
        shutil.copytree(STARTER, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    except OSError as error:
        raise InitError(f"{target}: {error}") from error
