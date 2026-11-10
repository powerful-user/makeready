"""Talk to the Mac over SSH: AppleScript, JavaScript, and shell commands."""

import shlex
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

INDESIGN = "com.adobe.InDesign"
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]


class MacError(Exception):
    """A command on the Mac failed."""


class MacUnreachable(MacError):
    """SSH could not connect to the Mac."""


class MacNotResponding(MacError):
    """InDesign did not answer in time."""


class MacBusy(MacError):
    """An earlier render may still be running on the Mac."""


def as_string(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _arguments(args: list[str]) -> str:
    return "{" + ", ".join(as_string(arg) for arg in args) + "}"


def js_call(source: str, args: list[str], timeout_s: int) -> str:
    return "\n".join(
        [
            f"with timeout of {timeout_s} seconds",
            f'tell application id "{INDESIGN}"',
            f"do script {as_string(source)} language javascript with arguments {_arguments(args)}",
            "end tell",
            "end timeout",
        ]
    )


def file_call(path: str, args: list[str], timeout_s: int) -> str:
    line = (
        f"do script (POSIX file {as_string(path)}) "
        f"language javascript with arguments {_arguments(args)}"
    )
    return "\n".join(
        [
            f"with timeout of {timeout_s} seconds",
            f'tell application id "{INDESIGN}"',
            line,
            "end tell",
            "end timeout",
        ]
    )


def _run(argv: list[str], stdin: str, timeout: float | None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            input=stdin,
            text=True,
            capture_output=True,
            timeout=timeout,
            errors="replace",
        )
    except OSError as error:
        raise MacError(f"{argv[0]}: {error}") from error


def _result(result: subprocess.CompletedProcess[str], *, applescript: bool) -> str:
    if result.returncode == 255:
        raise MacUnreachable(result.stderr.strip())
    if result.returncode != 0:
        detail = result.stderr.strip()
        if applescript and "-1712" in result.stderr:
            raise MacNotResponding(detail)
        raise MacError(detail)
    return result.stdout.strip()


def run_applescript(
    host: str,
    script: str,
    timeout_s: int,
    ssh: str = "ssh",
    prefix: Sequence[str] = (),
    grace_s: float = 30,
) -> str:
    remote = shlex.join([*prefix, "osascript", "-"])
    wait = timeout_s + grace_s
    try:
        result = _run([ssh, *SSH_OPTS, host, remote], script, wait)
    except subprocess.TimeoutExpired:
        raise MacNotResponding(f"no answer from the Mac within {wait:g} seconds") from None
    return _result(result, applescript=True)


def run_shell(host: str, argv: list[str], ssh: str = "ssh") -> str:
    result = _run([ssh, *SSH_OPTS, host, shlex.join(argv)], "", None)
    return _result(result, applescript=False)


def run_script(host: str, script: str, ssh: str = "ssh") -> str:
    result = _run([ssh, *SSH_OPTS, host, "sh -s"], script, None)
    return _result(result, applescript=False)


# ES3. The comment is how the fake Mac, and a reader, recognise the probe.
# The last expression is what InDesign's do script returns.
PROBE_JS = r"""// aid-probe
var report = app.version;
var i;
for (i = 0; i < app.documents.length; i++) {
    var label = app.documents[i].extractLabel("aid.job");
    if (label !== "") {
        report = report + "\n" + label + "\t" + app.documents[i].name;
    }
}
report;
"""


@dataclass(frozen=True)
class Probe:
    version: str
    jobs: list[str]


def probe(host: str, ssh: str = "ssh") -> Probe:
    text = run_applescript(host, js_call(PROBE_JS, [], 90), 90, ssh=ssh)
    lines = text.splitlines()
    version = lines[0].strip() if lines else ""
    if not version:
        raise MacError("InDesign probe returned no version")
    jobs = [line for line in lines[1:] if line]
    return Probe(version=version, jobs=jobs)
