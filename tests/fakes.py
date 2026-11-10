"""A fake Mac for tests: fake ssh and rsync executables over a local directory."""

import json
from pathlib import Path

FAKE_SSH = r'''#!/usr/bin/env python3
import json, os, re, shlex, shutil, sys, pathlib
root = pathlib.Path(os.environ["FAKE_ROOT"])
argv = sys.argv[1:]
data = sys.stdin.read()
with open(root / "ssh.log", "a") as log:
    log.write(json.dumps({"argv": argv, "stdin": data}) + "\n")
scen = json.loads(os.environ.get("FAKE_SCENARIO") or "{}")
i = 0
while i < len(argv) and argv[i] == "-o":
    i += 2
host = argv[i]
words = shlex.split(" ".join(argv[i + 1:]))
if scen.get("unreachable"):
    sys.stderr.write("ssh: connect to host " + host + " port 22: Connection refused\n")
    sys.exit(255)

def loc(p):
    return root / p.lstrip("/")

def timeout():
    sys.stderr.write("execution error: Adobe InDesign got an error: AppleEvent timed out. (-1712)\n")
    sys.exit(1)

def script_args(text):
    m = re.search(r"with arguments \{(.*)\}", text)
    if not m:
        return []
    found = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))
    return [a.replace('\\"', '"').replace("\\\\", "\\") for a in found]

while words[:1] == ["caffeinate"]:
    words = words[2:]
if words[:2] == ["mkdir", "-p"]:
    for p in words[2:]:
        loc(p).mkdir(parents=True, exist_ok=True)
    sys.exit(0)
if words[:1] == ["cp"]:
    shutil.copyfile(loc(words[1]), loc(words[2]))
    sys.exit(0)
if words[:1] == ["cat"]:
    sys.stdout.write(loc(words[1]).read_text())
    sys.exit(0)
if words[:1] == ["open"] or words[:2] == ["sh", "-s"]:
    sys.exit(0)
if words != ["osascript", "-"]:
    sys.stderr.write("fake ssh: unexpected command " + repr(words) + "\n")
    sys.exit(127)
if "aid-probe" in data:
    if scen.get("probe") == "timeout":
        timeout()
    if scen.get("probe") == "empty":
        print("")
        sys.exit(0)
    print(scen.get("version", "20.0.1"))
    for job in scen.get("jobs", []):
        print(job)
    sys.exit(0)
args = script_args(data)
if "render.jsx" in data:
    mode = scen.get("render", "ok")
    if mode == "timeout":
        timeout()
    if mode == "dropped":
        sys.stderr.write("Connection to " + host + " closed by remote host.\n")
        sys.exit(255)
    job, doc, build_id, job_id = args[:4]
    out = loc(job) / "out"
    out.mkdir(parents=True, exist_ok=True)
    if mode not in ("no_pdf", "open_fails"):
        shutil.copyfile(os.environ["FAKE_PDF"], out / (doc + ".pdf"))
    if mode != "open_fails":
        (out / (doc + ".indd")).write_bytes(b"indd")
    if mode == "bad_json":
        (out / "report.json").write_text("{")
    elif mode != "no_report":
        report = {
            "job": job_id, "doc": "/".join(pathlib.PurePosixPath(job).parts[-2:]),
            "build_id": "0000000000000000" if mode == "wrong_build" else build_id,
            "indesign": scen.get("version", "20.0.1"),
            "started": "2026-10-16T00:00:00Z", "finished": "2026-10-16T00:01:00Z",
            "status": scen.get("status", "ok"), "overset": [], "missing_links": [],
            "missing_fonts": [], "inks": ["Process Cyan", "Process Magenta", "Process Yellow", "Process Black"],
            "preflight": [], "error": None,
        }
        if mode == "open_fails":
            report.update(status="failed", inks=[], error="Cannot open the file \u201c" + doc + ".idml\u201d.")
        (out / "report.json").write_text(json.dumps(report))
    sys.exit(0)
if "package.jsx" in data:
    job, doc, dest = args[:3]
    d = loc(dest)
    d.mkdir(parents=True, exist_ok=True)
    missing = ["Nimbus Sans\tBold"] if scen.get("package") == "missing_font" else []
    report = {"status": "failed" if missing else "ok", "missing_fonts": missing, "missing_links": [], "error": None}
    (d / "package-report.json").write_text(json.dumps(report))
    sys.exit(0)
if "make-reference.jsx" in data:
    o = loc(args[1])
    o.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(os.environ["FAKE_IDML"], o / "reference.idml")
    sys.exit(0)
if "gen-schema.jsx" in data:
    d = loc(args[0])
    d.mkdir(parents=True, exist_ok=True)
    (d / "designmap.rnc").write_text("start = element Document { empty }\n")
    sys.exit(0)
if "font-check.jsx" in data:
    if scen.get("font_check") == "empty":
        sys.exit(0)
    honoured = "/fontcheck/" in args[0] and scen.get("document_fonts", True)
    print("Nimbus Sans\tRegular\t" + ("INSTALLED" if honoured else "NOT_AVAILABLE"))
    sys.exit(0)
sys.stderr.write("fake ssh: unexpected script\n")
sys.exit(1)
'''

FAKE_RSYNC = r'''#!/usr/bin/env python3
import json, os, sys, shutil, pathlib
root = pathlib.Path(os.environ["FAKE_ROOT"])
args = sys.argv[1:]
with open(root / "rsync.log", "a") as log:
    log.write(json.dumps(args) + "\n")

def loc(p):
    if not p.startswith("/") and ":" in p:
        return root / p.split(":", 1)[1].lstrip("/")
    return pathlib.Path(p)

src, dst = loc(args[-2]), loc(args[-1])
if "--delete" in args and dst.exists():
    shutil.rmtree(dst)
shutil.copytree(src, dst, dirs_exist_ok=True)
'''


def _executable(path: Path, source: str) -> str:
    path.write_text(source)
    path.chmod(0o755)
    return str(path)


class Mac:
    def __init__(self, tmp_path, monkeypatch, **scenario):
        self.root = tmp_path / "mac"
        self.root.mkdir()
        bindir = tmp_path / "bin"
        bindir.mkdir()
        self.ssh = _executable(bindir / "ssh", FAKE_SSH)
        self.rsync = _executable(bindir / "rsync", FAKE_RSYNC)
        self.monkeypatch = monkeypatch
        monkeypatch.setenv("FAKE_ROOT", str(self.root))
        self.set(**scenario)

    def set(self, **scenario):
        self.monkeypatch.setenv("FAKE_SCENARIO", json.dumps(scenario))

    def path(self, remote: str) -> Path:
        return self.root / remote.lstrip("/")

    def ssh_calls(self) -> list[dict]:
        log = self.root / "ssh.log"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def rsync_calls(self) -> list[list[str]]:
        log = self.root / "rsync.log"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
