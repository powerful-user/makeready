import re
import subprocess
from pathlib import Path

import makeready  # noqa: F401  (the red fails here until the package exists)

MAC = Path(__file__).resolve().parents[1] / "mac"
NOT_ES3 = [
    r"\blet\s", r"\bconst\s", r"=>", r"`", r"\.forEach\(", r"\.trim\(\)", r"\bclass\s", r"\bJSON\.",
    r"\.map\(", r"\.filter\(", r"\.reduce\(", r"\.some\(", r"\.every\(", r"\bObject\.keys\(",
    r"\bArray\.isArray\(", r"\.toISOString\(", r"\bDate\.now\(",
]


def jsx_files():
    files = sorted(MAC.glob("*.jsx"))
    assert files, "mac/ holds no .jsx files"
    return files


def test_required_scripts_exist():
    names = {p.name for p in jsx_files()}
    assert {"make-reference.jsx", "gen-schema.jsx", "font-check.jsx"} <= names


def test_es3_only():
    for path in jsx_files():
        text = path.read_text()
        for pattern in NOT_ES3:
            assert not re.search(pattern, text), f"{path.name} uses {pattern}"


def test_parses(tmp_path):
    for path in jsx_files():
        copy = tmp_path / (path.stem + ".js")
        copy.write_text(path.read_text())
        result = subprocess.run(["node", "--check", str(copy)], capture_output=True, text=True)
        assert result.returncode == 0, f"{path.name}: {result.stderr}"


def test_make_reference_content():
    text = (MAC / "make-reference.jsx").read_text()
    for needle in [
        "ExportFormat.INDESIGN_MARKUP",
        'insertLabel("aid.id"',
        "ColorModel.SPOT",
        "ColorModel.PROCESS",
        "rotationAngle",
        "place(",
        "Nimbus Sans",
        "SaveOptions.NO",
        "UserInteractionLevels.NEVER_INTERACT",
        "finally",
    ]:
        assert needle in text, needle


def test_gen_schema_and_font_check():
    assert "generateIDMLSchema" in (MAC / "gen-schema.jsx").read_text()
    text = (MAC / "font-check.jsx").read_text()
    for needle in [".fonts", ".status.toString()", "SaveOptions.NO", "finally"]:
        assert needle in text, needle


def test_script_preferences_changes_are_inside_try():
    assign = re.compile(r"app\.scriptPreferences\.\w+\s*=")
    restore = re.compile(
        r"finally\s*\{\s*"
        r"app\.scriptPreferences\.userInteractionLevel = savedInteraction;\s*"
        r"\}"
    )
    for path in jsx_files():
        text = path.read_text()
        head, sep, _rest = text.partition("try {")
        assert sep, f"{path.name} has no try"
        for line in head.splitlines():
            assert not assign.search(line), (
                f"{path.name} changes scriptPreferences before try: {line.strip()}"
            )
        assert restore.search(text), (
            f"{path.name} does not restore userInteractionLevel in its own finally"
        )
