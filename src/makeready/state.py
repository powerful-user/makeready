"""The render lock, and the marker that a render may still be running."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import fcntl

from makeready.mac import MacBusy, MacNotResponding, Probe, probe


class Busy(Exception):
    """Another render already holds the lock."""


def busy_path(cache_dir: Path) -> Path:
    return Path(cache_dir) / "mac-busy"


def mark_busy(cache_dir: Path) -> None:
    directory = Path(cache_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    busy_path(directory).write_text(stamp)


def clear_busy(cache_dir: Path) -> None:
    path = busy_path(cache_dir)
    if path.is_file():
        path.unlink()


def is_busy(cache_dir: Path) -> bool:
    return busy_path(cache_dir).exists()


@contextmanager
def render_lock(cache_dir: Path) -> Iterator[None]:
    directory = Path(cache_dir)
    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(directory / "render.lock", os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise Busy("render already running") from None
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def check_mac(host: str, cache_dir: Path, ssh: str = "ssh") -> Probe:
    try:
        found = probe(host, ssh=ssh)
    except MacNotResponding:
        if is_busy(cache_dir):
            raise MacBusy(
                "an earlier render may still be running on the Mac; "
                "wait, or run makeready mac-reset if it is stuck"
            ) from None
        raise MacNotResponding(
            "InDesign busy, still starting, or not responding; check the Mac screen and try again"
        ) from None
    clear_busy(cache_dir)
    return found
