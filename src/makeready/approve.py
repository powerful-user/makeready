"""Approve a reviewed render, reset a stuck Mac, and open a PDF in Preview."""

import json
import os
import re
from pathlib import Path

from makeready.mac import MacError, file_call, run_applescript, run_script, run_shell
from makeready.manifest import build_id, compute
from makeready.paths import handoff_dir, job_dir, scripts_dir
from makeready.render import _report_types, latest_build, render_dir
from makeready.spec import SpecError, load_template, resolve, set_status
from makeready.state import check_mac, clear_busy, render_lock
from makeready.sync import push

_NAME = re.compile(r"^[a-z0-9-]+$")
_PACKAGE_TIMEOUT = 600

# Quit InDesign without saving, force-quit if it is still there, and move
# crash recovery aside. The caller clears mac-busy only after this returns.
_RESET = """osascript <<'APPLESCRIPT' || true
with timeout of 30 seconds
tell application id "com.adobe.InDesign"
    close every document saving no
    quit
end tell
end timeout
APPLESCRIPT
i=0
while [ "$i" -lt 30 ]
do
    if ! pgrep -f "Adobe InDesign" >/dev/null 2>&1
    then
        break
    fi
    sleep 1
    i=$((i + 1))
done
if pgrep -f "Adobe InDesign" >/dev/null 2>&1
then
    pkill -9 -f "Adobe InDesign" || true
fi
stamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
n=0
mkdir -p "$HOME/makeready"
for dir in "$HOME/Library/Caches/Adobe InDesign"/Version*/*/"InDesign Recovery"
do
    if [ -d "$dir" ]
    then
        mv "$dir" "$HOME/makeready/recovery-${stamp}-${n}" || exit 1
        n=$((n + 1))
    fi
done
"""


class ApproveError(Exception):
    """The reviewed render cannot be approved."""


def approve(repo, cfg, series, doc, ssh="ssh", rsync="rsync") -> None:
    """Package the latest successful render and mark the document approved."""
    repo = Path(repo)
    try:
        _approve_locked(repo, cfg, series, doc, ssh, rsync)
    except OSError as error:
        where = error.filename or cfg.cache_dir
        raise ApproveError(f"{where}: {error}") from error


def _approve_locked(repo, cfg, series, doc, ssh, rsync) -> None:
    with render_lock(cfg.cache_dir):
        check_mac(cfg.mac_host, cfg.cache_dir, ssh=ssh)
        build_id_value = latest_build(repo, series, doc)
        out = render_dir(repo, series, doc, build_id_value)
        report_path = out / "report.json"
        if not report_path.is_file() or not _inside(out, report_path):
            raise ApproveError("the latest render did not succeed; render again")
        report = _read_json(report_path, "report")
        problem = _report_types(report_path, report)
        if problem is not None:
            raise ApproveError(problem)
        status = report.get("status")
        reported = report.get("build_id")
        if (
            not isinstance(status, str)
            or status not in ("ok", "warnings")
            or not isinstance(reported, str)
            or reported != build_id_value
        ):
            raise ApproveError("the latest render did not succeed; render again")
        if not isinstance(report.get("colour"), list):
            raise ApproveError("the latest render did not succeed; render again")
        pdf = out / f"{doc}.pdf"
        if not pdf.is_file() or not _inside(out, pdf):
            raise ApproveError("the latest render did not succeed; render again")
        if reported != _current_id(repo, cfg, series, doc):
            raise ApproveError("inputs changed since the reviewed render; build and render again")
        job = job_dir(cfg, series, doc)
        remote_report = f"{job}/out/report.json"
        try:
            remote_text = run_shell(cfg.mac_host, ["cat", remote_report], ssh=ssh)
        except MacError as error:
            raise ApproveError(f"{remote_report}: {error}") from error
        _require_mac_render(remote_report, remote_text, build_id_value, report["job"])
        push(repo / "mac", cfg.mac_host, scripts_dir(cfg), ssh=ssh, rsync=rsync)
        handoff = handoff_dir(cfg, series, doc)
        script = file_call(
            f"{scripts_dir(cfg)}/package.jsx",
            [job, doc, handoff],
            _PACKAGE_TIMEOUT,
        )
        run_applescript(cfg.mac_host, script, _PACKAGE_TIMEOUT, ssh=ssh)
        remote = f"{handoff}/package-report.json"
        try:
            text = run_shell(cfg.mac_host, ["cat", remote], ssh=ssh)
        except MacError as error:
            raise ApproveError(f"{remote}: {error}") from error
        package = _package_report(remote, text)
        problem = _write_text(out / "package-report.json", text)
        if problem is not None:
            raise ApproveError(problem)
        if package["status"] != "ok" or package["missing_fonts"] or package["missing_links"]:
            raise ApproveError(_package_message(package))
        set_status(repo, series, doc, "approved")


def mac_reset(cfg, ssh="ssh") -> None:
    """Quit InDesign, move its crash recovery aside, and clear the busy marker."""
    run_script(cfg.mac_host, _RESET, ssh=ssh)
    try:
        clear_busy(cfg.cache_dir)
    except OSError as error:
        where = error.filename or cfg.cache_dir
        raise ApproveError(f"{where}: {error}") from error


def open_pdf(cfg, series, doc, ssh="ssh") -> None:
    """Open the rendered PDF in Preview on the Mac."""
    _names(series, doc)
    run_shell(cfg.mac_host, ["open", f"{job_dir(cfg, series, doc)}/out/{doc}.pdf"], ssh=ssh)


def _current_id(repo: Path, cfg, series: str, doc: str) -> str:
    resolved = resolve(repo, series, doc)
    template = load_template(repo, resolved.template)
    return build_id(compute(repo, cfg, resolved, template))


def _require_mac_render(source: str, text: str, build_id_value: str, job: str) -> None:
    """The job folder on the Mac must still be the render being approved."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise ApproveError("the Mac holds a different render; render again") from None
    if not isinstance(data, dict):
        raise ApproveError("the Mac holds a different render; render again")
    problem = _report_types(source, data)
    if problem is not None:
        raise ApproveError(problem)
    status = data.get("status")
    reported = data.get("build_id")
    if (
        not isinstance(status, str)
        or not isinstance(reported, str)
        or reported != build_id_value
        or data["job"] != job
        or status not in ("ok", "warnings")
    ):
        raise ApproveError("the Mac holds a different render; render again")


def _read_json(path: Path, what: str) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ApproveError(f"{path}: {error}") from error
    if not isinstance(data, dict):
        raise ApproveError(f"{path}: {what} must be an object")
    return data


def _package_report(source: str, text: str) -> dict:
    if not isinstance(text, str):
        raise ApproveError(f"{source}: package report must be text")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ApproveError(f"{source}: {error}") from error
    if not isinstance(data, dict):
        raise ApproveError(f"{source}: package report must be an object")
    status = data.get("status")
    if status not in ("ok", "failed"):
        raise ApproveError(f"{source}: status must be ok or failed")
    fonts = data.get("missing_fonts")
    links = data.get("missing_links")
    if not _strings(fonts):
        raise ApproveError(f"{source}: missing_fonts must be a list of names")
    if not _strings(links):
        raise ApproveError(f"{source}: missing_links must be a list of names")
    error = data.get("error", None)
    if error is not None and not isinstance(error, str):
        raise ApproveError(f"{source}: error must be text or null")
    return data


def _package_message(package: dict) -> str:
    parts = []
    if package["missing_fonts"]:
        parts.append("missing fonts: " + ", ".join(package["missing_fonts"]))
    if package["missing_links"]:
        parts.append("missing links: " + ", ".join(package["missing_links"]))
    error = package.get("error")
    if isinstance(error, str) and error:
        parts.append(error)
    if not parts:
        parts.append("packaging failed")
    return "; ".join(parts)


def _strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _write_text(path: Path, text: str) -> str | None:
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    owned = False
    try:
        if temporary.exists() or temporary.is_symlink():
            return f"{temporary}: temporary file already exists"
        if not _inside(path.parent, temporary):
            return f"{temporary}: path must stay inside {path.parent}"
        owned = True
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
        owned = False
    except OSError as error:
        return f"{path}: {error}"
    finally:
        if owned:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return None


def _names(series, doc) -> None:
    for name in (series, doc):
        if not isinstance(name, str) or _NAME.fullmatch(name) is None:
            shown = name if isinstance(name, str) else "name"
            raise SpecError(
                f"{shown}: series and document names are lowercase letters, digits and hyphens"
            )


def _inside(root: Path, path: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False
