import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import makeready  # noqa: F401  (the red fails here until the package exists)

SEED = Path(__file__).resolve().parents[1] / "seed"


def schema_for(mapping, name):
    if name in mapping:
        return mapping[name]
    prefixes = [k for k in mapping if k.endswith("/") and name.startswith(k)]
    assert prefixes, f"seed/schema-map.json has no entry for {name}"
    return mapping[max(prefixes, key=len)]


def test_reference_idml_package():
    with zipfile.ZipFile(SEED / "reference.idml") as z:
        first = z.infolist()[0]
        assert first.filename == "mimetype"
        assert first.compress_type == zipfile.ZIP_STORED
        names = set(z.namelist())
    assert "designmap.xml" in names
    assert any(n.startswith("Spreads/") for n in names)
    assert any(n.startswith("Stories/") for n in names)


def test_reference_idml_structures():
    with zipfile.ZipFile(SEED / "reference.idml") as z:
        spreads = "".join(z.read(n).decode() for n in z.namelist() if n.startswith("Spreads/"))
        graphic = z.read("Resources/Graphic.xml").decode()
    for needle in ["<TextFrame", "<Rectangle", "<Oval", "<Image", "<PDF", "LinkResourceURI", 'Key="aid.id"']:
        assert needle in spreads, needle
    assert 'Model="Spot"' in graphic
    assert 'Model="Process"' in graphic


def test_schema_generated():
    schema = SEED / "schema"
    assert list(schema.rglob("*.rnc")) or list(schema.rglob("*.rng")), "seed/schema holds no schema files"


def test_schema_map_validates_reference(tmp_path):
    mapping = json.loads((SEED / "schema-map.json").read_text())
    for key in ("designmap.xml", "Spreads/", "Stories/"):
        assert mapping.get(key), key
    with zipfile.ZipFile(SEED / "reference.idml") as z:
        for name in z.namelist():
            if not name.endswith(".xml"):
                continue
            schema = schema_for(mapping, name)
            if schema is None:
                continue
            part = tmp_path / name
            part.parent.mkdir(parents=True, exist_ok=True)
            part.write_bytes(z.read(name))
            compact = ["-c"] if schema.endswith(".rnc") else []
            result = subprocess.run(
                ["jing", *compact, str(SEED / "schema" / schema), str(part)], capture_output=True, text=True
            )
            assert result.returncode == 0, f"{name}: {result.stdout}{result.stderr}"


def test_mac_facts():
    facts = json.loads((SEED / "mac-facts.json").read_text())
    assert set(facts) == {"indesign", "document_fonts_idml", "font_installed_systemwide"}
    assert facts["font_installed_systemwide"] is False


def test_jing_installed():
    assert shutil.which("jing"), "install jing: sudo apt install jing"
