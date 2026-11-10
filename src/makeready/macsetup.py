"""Build the reference IDML, the IDML schema, and the Document Fonts fact."""

import json
import re
import shutil
from pathlib import Path

from makeready.mac import MacError, file_call, run_applescript, run_shell
from makeready.paths import bootstrap_dir, scripts_dir
from makeready.state import check_mac
from makeready.sync import pull, push

_TIMEOUT = 300
_FONT = "Nimbus Sans\tRegular"


def _installed(output: str, idml: str) -> bool:
    family, style = _FONT.split("\t")
    found = False
    installed = False
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0] == family and parts[1] == style:
            if re.fullmatch(r"[A-Z_]+", parts[2]) is None:
                raise MacError(
                    f"font-check.jsx: status {parts[2]!r} for {family} {style} in {idml} "
                    "is not a FontStatus name"
                )
            found = True
            if parts[2] == "INSTALLED":
                installed = True
    if not found:
        raise MacError(f"font-check.jsx: no {family} {style} row for {idml}")
    return installed


def _run_jsx(cfg, name: str, args: list[str], ssh: str) -> str:
    script = file_call(f"{scripts_dir(cfg)}/{name}", args, _TIMEOUT)
    return run_applescript(cfg.mac_host, script, _TIMEOUT, ssh=ssh)


def bootstrap(
    cfg,
    repo: Path,
    ssh: str = "ssh",
    rsync: str = "rsync",
    fixtures: Path | None = None,
) -> dict:
    repo = Path(repo)
    if fixtures is None:
        fixtures = repo / "tests" / "fixtures" / "bootstrap"
    found = check_mac(cfg.mac_host, cfg.cache_dir, ssh=ssh)
    root = bootstrap_dir(cfg)
    push(repo / "mac", cfg.mac_host, scripts_dir(cfg), ssh=ssh, rsync=rsync)
    push(Path(fixtures), cfg.mac_host, f"{root}/in", ssh=ssh, rsync=rsync)
    _run_jsx(cfg, "make-reference.jsx", [f"{root}/in", f"{root}/out"], ssh)
    _run_jsx(cfg, "gen-schema.jsx", [f"{root}/out/schema"], ssh)
    systemwide_idml = f"{root}/out/reference.idml"
    systemwide = _installed(_run_jsx(cfg, "font-check.jsx", [systemwide_idml], ssh), systemwide_idml)
    fontcheck = f"{root}/fontcheck"
    run_shell(cfg.mac_host, ["mkdir", "-p", f"{fontcheck}/Document Fonts"], ssh)
    run_shell(cfg.mac_host, ["cp", f"{root}/out/reference.idml", f"{fontcheck}/reference.idml"], ssh)
    run_shell(
        cfg.mac_host,
        ["cp", f"{root}/in/NimbusSans-Regular.otf", f"{fontcheck}/Document Fonts/NimbusSans-Regular.otf"],
        ssh,
    )
    with_fonts_idml = f"{fontcheck}/reference.idml"
    with_fonts = _installed(_run_jsx(cfg, "font-check.jsx", [with_fonts_idml], ssh), with_fonts_idml)
    pulled = repo / "build" / "bootstrap"
    pull(cfg.mac_host, f"{root}/out", pulled, ssh=ssh, rsync=rsync)
    seed = repo / "seed"
    seed.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(pulled / "reference.idml", seed / "reference.idml")
    schema = seed / "schema"
    if schema.exists():
        shutil.rmtree(schema)
    shutil.copytree(pulled / "schema", schema)
    facts = {
        "indesign": found.version,
        "document_fonts_idml": with_fonts,
        "font_installed_systemwide": systemwide,
    }
    (seed / "mac-facts.json").write_text(json.dumps(facts, indent=2, sort_keys=True) + "\n")
    return facts
