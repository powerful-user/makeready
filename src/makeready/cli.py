"""The makeready command line."""

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from makeready import __version__
from makeready.approve import ApproveError, approve, mac_reset, open_pdf
from makeready.build import BuildFailed, build
from makeready.config import ConfigError, load, token_warning
from makeready.figma import FigmaClient, FigmaError
from makeready.importer import import_figma
from makeready.mac import MacError, MacUnreachable
from makeready.macsetup import bootstrap
from makeready.proof import make_proof
from makeready.render import latest_build, render, render_dir, render_many
from makeready.spec import SpecError, list_docs, resolve
from makeready.state import Busy, check_mac


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="makeready")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--version", action="store_true")
    commands = parser.add_subparsers(dest="command")
    mac = commands.add_parser("mac")
    mac_commands = mac.add_subparsers(dest="mac_command")
    mac_commands.add_parser("check")
    mac_commands.add_parser("bootstrap")
    import_command = commands.add_parser("import-figma")
    import_command.add_argument("key")
    import_command.add_argument("--name", required=True)
    import_command.add_argument("--frames")
    import_command.add_argument("--version", dest="figma_version")
    import_command.add_argument("--scale", type=float, default=1.0)
    build_command = commands.add_parser("build")
    build_command.add_argument("target")
    build_command.add_argument("--reopen", action="store_true")
    render_command = commands.add_parser("render")
    render_command.add_argument("target")
    render_command.add_argument("--build", dest="build_id")
    render_command.add_argument("--reopen", action="store_true")
    proof_command = commands.add_parser("proof")
    proof_command.add_argument("target")
    approve_command = commands.add_parser("approve")
    approve_command.add_argument("target")
    open_command = commands.add_parser("open")
    open_command.add_argument("target")
    commands.add_parser("mac-reset")
    args = parser.parse_args(argv)
    if args.version:
        print(f"makeready {__version__}")
        return 0
    try:
        return _dispatch(args)
    except MacUnreachable as error:
        print(f"Mac unreachable: {error}", file=sys.stderr)
        return 2
    except (ConfigError, MacError, FigmaError, ValueError, SpecError, Busy) as error:
        print(error, file=sys.stderr)
        return 2


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "import-figma":
        return _import_figma(args)
    if args.command == "build":
        return _build(args)
    if args.command == "render":
        return _render(args)
    if args.command == "proof":
        return _proof(args)
    if args.command == "approve":
        return _approve(args)
    if args.command == "open":
        return _open(args)
    if args.command == "mac-reset":
        return _mac_reset(args)
    if args.command != "mac" or args.mac_command not in ("check", "bootstrap"):
        return 2
    cfg = load()
    ssh = os.environ.get("MAKEREADY_SSH", "ssh")
    rsync = os.environ.get("MAKEREADY_RSYNC", "rsync")
    if args.mac_command == "check":
        found = check_mac(cfg.mac_host, cfg.cache_dir, ssh=ssh)
        print(f"InDesign {found.version}")
        for job in found.jobs:
            print(job)
        return 0
    facts = bootstrap(cfg, Path(args.repo), ssh=ssh, rsync=rsync)
    print(json.dumps(facts, indent=2, sort_keys=True))
    return 0


def _import_figma(args: argparse.Namespace) -> int:
    cfg = load()
    client = FigmaClient(cfg.figma_token)
    warning = token_warning(cfg, date.today())
    if warning:
        print(warning, file=sys.stderr)
    frames = None
    if args.frames:
        frames = [name.strip() for name in args.frames.split(",") if name.strip()]
    report = import_figma(
        Path(args.repo),
        client,
        args.key,
        args.name,
        frames=frames,
        version=args.figma_version,
        scale=args.scale,
    )
    for line in report:
        print(line)
    if any(line.startswith("error:") for line in report):
        return 1
    return 0


def _build(args: argparse.Namespace) -> int:
    try:
        cfg = load()
    except OSError as error:
        print(error, file=sys.stderr)
        return 2
    repo = Path(args.repo)
    series, docs, problem = _build_targets(repo, args.target)
    if problem is not None:
        print(problem, file=sys.stderr)
        return 2
    status = 0
    for doc in docs:
        code = _build_one(repo, cfg, series, doc, args.reopen)
        if code == 2:
            status = 2
        elif code == 1 and status == 0:
            status = 1
    return status


def _build_targets(repo: Path, target: str, command: str = "build") -> tuple[str, list[str], str | None]:
    if not isinstance(target, str) or target.count("/") > 1 or "\\" in target or "\x00" in target:
        return "", [], f"{command} expects SERIES or SERIES/DOC"
    if "/" in target:
        series, doc = target.split("/", 1)
        return series, [doc], None
    try:
        return target, list_docs(repo, target), None
    except SpecError as error:
        return target, [], str(error)


def _build_one(repo: Path, cfg, series: str, doc: str, reopen: bool) -> int:
    try:
        if reopen:
            resolved = resolve(repo, series, doc)
            if resolved.status == "approved":
                print(
                    f"{series}/{doc} is approved; makeready build --reopen discards hand edits to its packaged .indd",
                    file=sys.stderr,
                )
        result = build(repo, cfg, series, doc, reopen=reopen)
    except BuildFailed as error:
        print(f"{series}/{doc}", file=sys.stderr)
        for finding in error.findings:
            element = f" [{finding.element}]" if finding.element else ""
            print(f"{finding.level}{element}: {finding.message}", file=sys.stderr)
        for line in error.errors:
            print(line, file=sys.stderr)
        return 1
    except SpecError as error:
        print(error, file=sys.stderr)
        return 2
    except OSError as error:
        print(f"{series}/{doc}", file=sys.stderr)
        print(error, file=sys.stderr)
        return 1
    print(f"{series}/{doc} {result.build_id}")
    for finding in result.findings:
        if finding.level != "warning":
            continue
        element = f" [{finding.element}]" if finding.element else ""
        print(f"warning{element}: {finding.message}")
    return 0


def _render(args: argparse.Namespace) -> int:
    cfg = _loaded()
    if cfg is None:
        return 2
    repo = Path(args.repo)
    series, docs, problem = _build_targets(repo, args.target, "render")
    if problem is not None:
        print(problem, file=sys.stderr)
        return 2
    if args.build_id is not None and "/" not in args.target:
        print("render --build needs SERIES/DOC", file=sys.stderr)
        return 2
    ssh, rsync = _transport()
    if args.reopen:
        _warn_reopen(repo, series, docs)
    try:
        return _render_targets(args, repo, cfg, series, docs, ssh, rsync)
    except OSError as error:
        print(error, file=sys.stderr)
        return 1


def _render_targets(args, repo, cfg, series, docs, ssh, rsync) -> int:
    if "/" in args.target:
        result = render(
            repo,
            cfg,
            series,
            docs[0],
            build_id=args.build_id,
            ssh=ssh,
            rsync=rsync,
            reopen=args.reopen,
        )
        return _print_results([result])
    return _print_results(
        render_many(repo, cfg, series, docs, ssh=ssh, rsync=rsync, reopen=args.reopen)
    )


def _proof(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    series, doc, problem = _one_target(args.target, "proof")
    if problem is not None:
        print(problem, file=sys.stderr)
        return 2
    resolved = resolve(repo, series, doc)
    build_id = latest_build(repo, series, doc)
    folder = render_dir(repo, series, doc, build_id)
    pdf = folder / f"{doc}.pdf"
    if not _inside(folder, pdf) or pdf.is_symlink() or not pdf.is_file():
        print(f"render failed: no {doc}.pdf", file=sys.stderr)
        return 1
    geometry = _read_geometry(repo / "build" / series / doc / build_id / "geometry.json")
    try:
        paths = make_proof(pdf, geometry, folder / "proof", reference_dir=_reference_dir(repo, resolved.template))
    except (RuntimeError, OSError) as error:
        print(error, file=sys.stderr)
        return 1
    for path in paths:
        print(path)
    return 0


def _approve(args: argparse.Namespace) -> int:
    cfg = _loaded()
    if cfg is None:
        return 2
    repo = Path(args.repo)
    series, doc, problem = _one_target(args.target, "approve")
    if problem is not None:
        print(problem, file=sys.stderr)
        return 2
    ssh, rsync = _transport()
    try:
        approve(repo, cfg, series, doc, ssh=ssh, rsync=rsync)
    except ApproveError as error:
        print(error, file=sys.stderr)
        return 1
    print(f"{series}/{doc} approved")
    return 0


def _open(args: argparse.Namespace) -> int:
    cfg = _loaded()
    if cfg is None:
        return 2
    series, doc, problem = _one_target(args.target, "open")
    if problem is not None:
        print(problem, file=sys.stderr)
        return 2
    ssh, _rsync = _transport()
    open_pdf(cfg, series, doc, ssh=ssh)
    return 0


def _mac_reset(args: argparse.Namespace) -> int:
    cfg = _loaded()
    if cfg is None:
        return 2
    ssh, _rsync = _transport()
    try:
        mac_reset(cfg, ssh=ssh)
    except (OSError, ApproveError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


def _print_results(results) -> int:
    status = 0
    for result in results:
        print(f"{result.series}/{result.doc} {result.status}")
        if result.message:
            print(result.message)
        if result.pdf is not None:
            print(result.pdf)
        if result.status in ("failed", "timeout"):
            status = 1
    return status


def _warn_reopen(repo: Path, series: str, docs: list[str]) -> None:
    for doc in docs:
        if resolve(repo, series, doc).status == "approved":
            print(
                f"{series}/{doc} is approved; makeready render --reopen discards hand edits to its packaged .indd",
                file=sys.stderr,
            )


def _one_target(target: str, command: str) -> tuple[str, str, str | None]:
    if not isinstance(target, str) or target.count("/") != 1 or "\\" in target or "\x00" in target:
        return "", "", f"{command} expects SERIES/DOC"
    series, doc = target.split("/", 1)
    if not series or not doc:
        return "", "", f"{command} expects SERIES/DOC"
    return series, doc, None


def _read_geometry(path: Path) -> dict:
    root = path.parent
    try:
        if path.is_symlink() or not path.is_file() or not _inside(root, path):
            raise SpecError(f"{path}: geometry not found")
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except SpecError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SpecError(f"{path}: {error}") from error
    if not isinstance(data, dict):
        raise SpecError(f"{path}: geometry must be an object")
    return data


def _reference_dir(repo: Path, template: str) -> Path | None:
    root = repo / "templates"
    folder = root / template / "reference"
    try:
        if not folder.is_dir():
            return None
        if folder.is_symlink() or not _inside(root, folder):
            raise SpecError(f"{folder}: path must stay inside {root}")
    except SpecError:
        raise
    except OSError as error:
        raise SpecError(f"{folder}: {error}") from error
    return folder


def _loaded():
    try:
        return load()
    except OSError as error:
        print(error, file=sys.stderr)
        return None


def _transport() -> tuple[str, str]:
    return os.environ.get("MAKEREADY_SSH", "ssh"), os.environ.get("MAKEREADY_RSYNC", "rsync")


def _inside(root: Path, path: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False
