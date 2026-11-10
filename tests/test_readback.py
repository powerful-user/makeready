"""The skill's read-back script: reports what a built IDML holds, read from the file."""

import json
import subprocess
import sys

from fixture_repo import ROOT, make_config, make_repo

from makeready.build import build

SCRIPT = ROOT / "skills" / "makeready" / "scripts" / "readback.py"
PANES = [
    [[0, 0], [0, 90], [180, 90], [180, 0]],
    [[270, 0], [270, 90], [450, 90], [450, 0]],
]


def built(tmp_path, change=None):
    repo = make_repo(tmp_path, real_seed=True)
    if change:
        path = repo / "templates" / "flyer" / "template.json"
        template = json.loads(path.read_text())
        change({e["id"]: e for page in template["pages"] for e in page["elements"]})
        path.write_text(json.dumps(template))
    result = build(repo, make_config(tmp_path), "spring", "garden")
    return result.path / "garden.idml"


def readback(idml, *args):
    run = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), str(idml), *args], capture_output=True, text=True, check=False
    )
    assert run.returncode == 0, run.stderr
    return run.stdout.splitlines()


def line(lines, start):
    return next(text for text in lines if text.lstrip().startswith(start))


def test_reports_pages_frames_text_and_images(tmp_path):
    lines = readback(built(tmp_path))
    assert lines[0] == "page 1: 8.5 x 11 in (612 x 792 pt), bleed 0.125 in"
    assert line(lines, "headline").split()[1:] == ["TextFrame", "x", "0.5", "y", "0.667", "w", "7.5", "h", "0.833", '"Garden', 'Party"']
    assert "image kitchen.jpg" in line(lines, "image ")
    assert '"Join us Saturday for coffee. / Tours every hour / Free parking"' in line(lines, "body")


def test_scale_gives_full_size_inches_and_lists_outlines(tmp_path):
    def change(elements):
        elements["photo:hero"].update(paths=PANES)
        elements["Band"].update(nonprinting=True)

    lines = readback(built(tmp_path, change), "--scale", "0.25")
    assert lines[0].startswith("page 1: 34 x 44 in (612 x 792 pt)")
    assert line(lines, "photo:hero").endswith("2 outlines")
    assert line(lines, "outline 1").split() == ["outline", "1", "x", "-0.5", "y", "16.667", "w", "10", "h", "5"]
    assert line(lines, "outline 2").split() == ["outline", "2", "x", "14.5", "y", "16.667", "w", "10", "h", "5"]
    assert line(lines, "Band").endswith("nonprinting")
