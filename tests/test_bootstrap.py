import json
import zipfile
from pathlib import Path

import pytest

from fakes import Mac

from makeready.config import Config
from makeready.mac import MacError
from makeready.macsetup import bootstrap

FIXTURES = Path(__file__).parent / "fixtures" / "bootstrap"


def config(tmp_path):
    return Config(
        figma_token=None, figma_token_expires=None, mac_host="h", mac_home="/Users/j",
        photos=tmp_path, fonts=tmp_path, print_profile="GRACoL2013_CRPC6",
        print_inks=("C", "M", "Y", "K"), cache_dir=tmp_path / "cache",
    )


def setup(tmp_path, monkeypatch, **scenario):
    mac = Mac(tmp_path, monkeypatch, version="20.0.1", **scenario)
    idml = tmp_path / "ref.idml"
    with zipfile.ZipFile(idml, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.adobe.indesign-idml-package")
        z.writestr("designmap.xml", "<Document/>")
    monkeypatch.setenv("FAKE_IDML", str(idml))
    repo = tmp_path / "repo"
    (repo / "mac").mkdir(parents=True)
    (repo / "mac" / "make-reference.jsx").write_text("// reference\n")
    return mac, repo, idml


def test_bootstrap(tmp_path, monkeypatch):
    mac, repo, idml = setup(tmp_path, monkeypatch)
    facts = bootstrap(config(tmp_path), repo, ssh=mac.ssh, rsync=mac.rsync, fixtures=FIXTURES)
    assert facts == {"indesign": "20.0.1", "document_fonts_idml": True, "font_installed_systemwide": False}
    assert (repo / "seed" / "reference.idml").read_bytes() == idml.read_bytes()
    assert (repo / "seed" / "schema" / "designmap.rnc").exists()
    assert json.loads((repo / "seed" / "mac-facts.json").read_text()) == facts
    targets = [call[-1] for call in mac.rsync_calls()]
    assert targets[:2] == ["h:/Users/j/makeready/scripts/", "h:/Users/j/makeready/bootstrap/in/"]
    scripts = [c["stdin"] for c in mac.ssh_calls() if "do script" in c["stdin"]]
    assert "aid-probe" in scripts[0]
    assert "make-reference.jsx" in scripts[1]
    assert '{"/Users/j/makeready/bootstrap/in", "/Users/j/makeready/bootstrap/out"}' in scripts[1]
    assert "gen-schema.jsx" in scripts[2]
    assert '"/Users/j/makeready/bootstrap/out/reference.idml"' in scripts[3]
    assert '"/Users/j/makeready/bootstrap/fontcheck/reference.idml"' in scripts[4]
    assert mac.path("/Users/j/makeready/bootstrap/fontcheck/Document Fonts/NimbusSans-Regular.otf").exists()


def test_bootstrap_rejects_empty_font_check(tmp_path, monkeypatch):
    mac, repo, _idml = setup(tmp_path, monkeypatch, font_check="empty")
    with pytest.raises(MacError, match="font-check.jsx") as caught:
        bootstrap(config(tmp_path), repo, ssh=mac.ssh, rsync=mac.rsync, fixtures=FIXTURES)
    assert "/Users/j/makeready/bootstrap/out/reference.idml" in str(caught.value)


def test_document_fonts_not_honoured(tmp_path, monkeypatch):
    mac, repo, _ = setup(tmp_path, monkeypatch, document_fonts=False)
    facts = bootstrap(config(tmp_path), repo, ssh=mac.ssh, rsync=mac.rsync, fixtures=FIXTURES)
    assert facts["document_fonts_idml"] is False


@pytest.mark.parametrize("status", ["", "1718181441", "installed", " INSTALLED"])
def test_font_check_status_must_be_an_enumeration_name(status):
    from makeready.macsetup import _installed

    with pytest.raises(MacError, match="font-check.jsx"):
        _installed(f"Nimbus Sans\tRegular\t{status}", "/x/reference.idml")


def test_font_check_known_statuses():
    from makeready.macsetup import _installed

    assert _installed("Nimbus Sans\tRegular\tINSTALLED", "/x/reference.idml") is True
    assert _installed("Nimbus Sans\tRegular\tNOT_AVAILABLE", "/x/reference.idml") is False
