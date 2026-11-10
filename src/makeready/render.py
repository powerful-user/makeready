"""Render a build on the Mac and check the PDF that comes back."""

import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from makeready.colourcheck import check as colour_check
from makeready.mac import MacError, MacNotResponding, file_call, run_applescript
from makeready.paths import job_dir, scripts_dir
from makeready.spec import SpecError, resolve
from makeready.state import check_mac, mark_busy, render_lock
from makeready.sync import pull, push

_BUILD_ID = re.compile(r"^[0-9a-f]{16}$")
_NAME = re.compile(r"^[a-z0-9-]+$")
_REPORT_STATUS = {"ok", "warnings", "failed"}


@dataclass(frozen=True)
class RenderResult:
    series: str
    doc: str
    build_id: str
    status: str
    report: dict | None
    message: str
    pdf: Path | None


def render_dir(repo, series, doc, build_id) -> Path:
    """Where a build's render outputs live, beside the immutable build."""
    _names(series, doc)
    if not isinstance(build_id, str) or _BUILD_ID.fullmatch(build_id) is None:
        raise SpecError(f"{build_id}: build id must be 16 hex characters")
    return Path(repo) / "build" / series / doc / f"{build_id}.render"


def latest_build(repo, series, doc) -> str:
    """The build the `latest` symlink names, or an error telling you to build."""
    _names(series, doc)
    link = Path(repo) / "build" / series / doc / "latest"
    try:
        if not link.is_symlink():
            raise SpecError(f"{series}/{doc}: no build; run makeready build")
        target = os.readlink(link)
    except OSError as error:
        raise SpecError(f"{link}: {error}") from error
    return _existing_build(repo, series, doc, target)


def render(
    repo,
    cfg,
    series,
    doc,
    build_id=None,
    ssh="ssh",
    rsync="rsync",
    timeout_s=600,
    reopen=False,
) -> RenderResult:
    """Render one document. Nothing touches the Mac before the lock is held."""
    repo = Path(repo)
    resolved = resolve(repo, series, doc)
    if resolved.status == "approved" and not reopen:
        raise SpecError(
            f"{series}/{doc} is approved; makeready render --reopen discards hand edits to its packaged .indd"
        )
    if build_id is None:
        build_id = latest_build(repo, series, doc)
    else:
        build_id = _existing_build(repo, series, doc, build_id)
    try:
        with render_lock(cfg.cache_dir):
            check_mac(cfg.mac_host, cfg.cache_dir, ssh=ssh)
            push(repo / "mac", cfg.mac_host, scripts_dir(cfg), ssh=ssh, rsync=rsync)
            return _render_locked(repo, cfg, series, doc, build_id, ssh, rsync, timeout_s)
    except OSError as error:
        return RenderResult(
            series, doc, build_id, "failed", None, _io_message(error, cfg.cache_dir), None
        )


def render_many(
    repo,
    cfg,
    series,
    docs: list[str],
    ssh="ssh",
    rsync="rsync",
    timeout_s=600,
    reopen=False,
) -> list[RenderResult]:
    """Render every document of a series under one lock. Stop after a timeout or a dropped call."""
    repo = Path(repo)
    if not isinstance(docs, list):
        raise SpecError("documents must be a list of names")
    prepared: list[tuple[str, str]] = []
    for doc in docs:
        resolved = resolve(repo, series, doc)
        if resolved.status == "approved" and not reopen:
            raise SpecError(
                f"{series}/{doc} is approved; makeready render --reopen discards hand edits to its packaged .indd"
            )
        prepared.append((doc, latest_build(repo, series, doc)))
    if not prepared:
        return []
    results: list[RenderResult] = []
    try:
        with render_lock(cfg.cache_dir):
            check_mac(cfg.mac_host, cfg.cache_dir, ssh=ssh)
            push(repo / "mac", cfg.mac_host, scripts_dir(cfg), ssh=ssh, rsync=rsync)
            stopped = False
            for doc, build_id in prepared:
                if stopped:
                    results.append(
                        RenderResult(
                            series,
                            doc,
                            build_id,
                            "failed",
                            None,
                            "not rendered: an earlier document in the batch did not finish",
                            None,
                        )
                    )
                    continue
                try:
                    result = _render_locked(repo, cfg, series, doc, build_id, ssh, rsync, timeout_s)
                except MacError as error:
                    results.append(RenderResult(series, doc, build_id, "failed", None, str(error), None))
                    stopped = True
                    continue
                except OSError as error:
                    results.append(
                        RenderResult(
                            series, doc, build_id, "failed", None, _io_message(error, cfg.cache_dir), None
                        )
                    )
                    continue
                results.append(result)
                if result.status == "timeout":
                    stopped = True
            return results
    except OSError as error:
        message = _io_message(error, cfg.cache_dir)
        done = {item.doc for item in results}
        for doc, build_id in prepared:
            if doc not in done:
                results.append(RenderResult(series, doc, build_id, "failed", None, message, None))
        return results


def _render_locked(repo, cfg, series, doc, build_id, ssh, rsync, timeout_s) -> RenderResult:
    """Push the build, render, pull and colour-check. The caller holds the lock."""
    job = job_dir(cfg, series, doc)
    push(repo / "build" / series / doc / build_id, cfg.mac_host, f"{job}/in", ssh=ssh, rsync=rsync)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    script = file_call(
        f"{scripts_dir(cfg)}/render.jsx",
        [job, doc, build_id, f"{build_id}-{stamp}", cfg.print_profile],
        timeout_s,
    )
    try:
        run_applescript(
            cfg.mac_host,
            script,
            timeout_s,
            ssh=ssh,
            prefix=["caffeinate", "-i"],
        )
    except MacNotResponding:
        problem = _mark_busy(cfg.cache_dir)
        return RenderResult(
            series,
            doc,
            build_id,
            "timeout",
            None,
            problem
            or "InDesign may still be working; wait and render again, or run makeready mac-reset if it is stuck",
            None,
        )
    except MacError as error:
        problem = _mark_busy(cfg.cache_dir)
        if problem is not None:
            raise MacError(problem) from error
        raise

    out = render_dir(repo, series, doc, build_id)
    try:
        pull(cfg.mac_host, f"{job}/out", out, ssh=ssh, rsync=rsync)
    except OSError as error:
        return RenderResult(series, doc, build_id, "failed", None, _io_message(error, out), None)
    report_path = out / "report.json"
    if not report_path.is_file() or not _inside(out, report_path):
        return RenderResult(series, doc, build_id, "failed", None, "render failed: no report.json", None)
    report, problem = _load_report(report_path)
    if problem is not None:
        return RenderResult(series, doc, build_id, "failed", None, problem, None)
    reported = report.get("build_id")
    if not isinstance(reported, str) or reported != build_id:
        shown = reported if isinstance(reported, str) else "none"
        message = f"report is for build {shown}, not {build_id}"
        _fail_report(report_path, report, message)
        return RenderResult(series, doc, build_id, "failed", report, message, None)
    if report.get("status") == "failed":
        error = report.get("error")
        message = error if isinstance(error, str) and error else "render failed on the Mac"
        return RenderResult(series, doc, build_id, "failed", report, message, None)
    pdf = out / f"{doc}.pdf"
    if not pdf.is_file() or not _inside(out, pdf):
        message = f"render failed: no {doc}.pdf"
        _fail_report(report_path, report, message)
        return RenderResult(series, doc, build_id, "failed", report, message, None)
    status, message = _apply_colour(report, pdf, cfg.print_inks, out / "colour-work")
    written = _write_report(report_path, report)
    if written is not None:
        return RenderResult(series, doc, build_id, "failed", report, written, pdf)
    return RenderResult(series, doc, build_id, status, report, message, pdf)


def _apply_colour(report: dict, pdf: Path, inks, work: Path) -> tuple[str, str]:
    try:
        findings = colour_check(pdf, inks, work)
    except RuntimeError as error:
        message = str(error)
        report["status"] = "failed"
        report["error"] = message
        return "failed", message
    except OSError as error:
        message = _io_message(error, work)
        report["status"] = "failed"
        report["error"] = message
        return "failed", message
    finally:
        _discard(work)
    colour = []
    for finding in findings:
        colour.append({"level": finding.level, "code": finding.code, "message": finding.message})
    report["colour"] = colour
    status = report.get("status")
    if not isinstance(status, str):
        report["status"] = "failed"
        report["error"] = "status must be text"
        return "failed", "status must be text"
    if status not in _REPORT_STATUS:
        status = "failed"
    errors = [item["message"] for item in colour if item["level"] == "error"]
    if errors:
        status = "failed"
        report["status"] = status
        report["error"] = "; ".join(errors)
        return status, "; ".join(errors)
    report["status"] = status
    return status, ""


def _io_message(error: OSError, fallback) -> str:
    where = error.filename or fallback
    return f"{where}: {error}"


def _mark_busy(cache_dir) -> str | None:
    try:
        mark_busy(cache_dir)
    except OSError as error:
        return _io_message(error, cache_dir)
    return None


def _fail_report(path: Path, report: dict, message: str) -> None:
    """Record a post-pull failure in a loaded report, keeping its other fields."""
    report["status"] = "failed"
    report["error"] = message
    _write_report(path, report)


def _load_report(path: Path) -> tuple[dict | None, str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return None, f"{path}: {error}"
    if not isinstance(data, dict):
        return None, f"{path}: report must be an object"
    problem = _report_types(path, data)
    if problem is not None:
        return None, problem
    return data, None


def _report_types(source, data: dict) -> str | None:
    """Type-check a render report before any of its fields are compared."""
    if not isinstance(data.get("job"), str):
        return f"{source}: job must be text"
    if not isinstance(data.get("build_id"), str):
        return f"{source}: build_id must be text"
    if not isinstance(data.get("status"), str):
        return f"{source}: status must be text"
    for field in ("overset", "missing_links", "missing_fonts", "inks"):
        value = data.get(field)
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            return f"{source}: {field} must be a list of text"
    preflight = data.get("preflight")
    if not isinstance(preflight, list) or any(not isinstance(item, dict) for item in preflight):
        return f"{source}: preflight must be a list of objects"
    error = data.get("error", None)
    if error is not None and not isinstance(error, str):
        return f"{source}: error must be text or null"
    return None


def _write_report(path: Path, report: dict) -> str | None:
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    owned = False
    try:
        if temporary.exists() or temporary.is_symlink():
            return f"{temporary}: temporary file already exists"
        if not _inside(path.parent, temporary):
            return f"{temporary}: path must stay inside {path.parent}"
        owned = True
        temporary.write_text(json.dumps(report) + "\n", encoding="utf-8")
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


def _discard(work: Path) -> None:
    try:
        if work.is_symlink() or work.is_file():
            work.unlink()
        elif work.is_dir():
            shutil.rmtree(work)
    except OSError:
        pass


def _existing_build(repo, series, doc, build_id) -> str:
    if not isinstance(build_id, str) or _BUILD_ID.fullmatch(build_id) is None:
        raise SpecError(f"{series}/{doc}: no build; run makeready build")
    directory = Path(repo) / "build" / series / doc / build_id
    root = Path(repo) / "build" / series / doc
    try:
        if not directory.is_dir() or not directory.resolve().is_relative_to(root.resolve()):
            raise SpecError(f"{series}/{doc}: no build; run makeready build")
    except OSError as error:
        raise SpecError(f"{directory}: {error}") from error
    return build_id


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
