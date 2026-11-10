"""Copy job folders with the rsync flags openrsync on macOS accepts."""

import shlex
import subprocess
from pathlib import Path

from makeready.mac import SSH_OPTS, MacError, run_shell


def _transport(ssh: str) -> str:
    return shlex.join([ssh, *SSH_OPTS])


def _rsync(argv: list[str]) -> None:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, errors="replace")
    except OSError as error:
        raise MacError(f"{argv[0]}: {error}") from error
    if result.returncode != 0:
        raise MacError(f"rsync failed: {result.stderr.strip()}")


def push(src: Path, host: str, remote_dir: str, ssh: str = "ssh", rsync: str = "rsync") -> None:
    run_shell(host, ["mkdir", "-p", remote_dir], ssh)
    _rsync(
        [
            rsync,
            "-rt",
            "--delete",
            "-e",
            _transport(ssh),
            f"{src}/",
            f"{host}:{remote_dir}/",
        ]
    )


def pull(host: str, remote_dir: str, dest: Path, ssh: str = "ssh", rsync: str = "rsync") -> None:
    dest.mkdir(parents=True, exist_ok=True)
    _rsync(
        [
            rsync,
            "-rt",
            "--delete",
            "-e",
            _transport(ssh),
            f"{host}:{remote_dir}/",
            f"{dest}/",
        ]
    )
