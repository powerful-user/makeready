"""POSIX paths on the Mac for scripts, jobs, handoff and bootstrap."""

from pathlib import PurePosixPath


def mac_root(cfg) -> str:
    return str(PurePosixPath(cfg.mac_home) / "makeready")


def scripts_dir(cfg) -> str:
    return str(PurePosixPath(mac_root(cfg)) / "scripts")


def job_dir(cfg, series: str, doc: str) -> str:
    return str(PurePosixPath(mac_root(cfg)) / "jobs" / series / doc)


def handoff_dir(cfg, series: str, doc: str) -> str:
    return str(PurePosixPath(mac_root(cfg)) / "handoff" / series / doc)


def bootstrap_dir(cfg) -> str:
    return str(PurePosixPath(mac_root(cfg)) / "bootstrap")
