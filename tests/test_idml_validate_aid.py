import re
import shutil
import zipfile

from fixture_repo import ROOT, make_config, make_repo

from makeready.build import build
from makeready.idml.validate import validate

AID = re.compile(rb"<\?aid\s[^>]*\?>")


def _built(tmp_path):
    repo, cfg = make_repo(tmp_path, real_seed=True), make_config(tmp_path)
    return build(repo, cfg, "spring", "garden").path / "garden.idml"


def _copy(src, dst, change):
    with zipfile.ZipFile(src) as source, zipfile.ZipFile(dst, "w") as package:
        for info in source.infolist():
            data = change(info.filename, source.read(info))
            if data is not None:
                package.writestr(info, data)


def test_validate_refuses_designmap_without_aid(tmp_path):
    bad = tmp_path / "bad.idml"
    _copy(_built(tmp_path), bad, lambda name, data: AID.sub(b"", data) if name == "designmap.xml" else data)
    errors = validate(bad, ROOT / "seed" / "schema")
    assert "designmap.xml: no <?aid?> instruction; InDesign will not open the package" in errors


def test_validate_refuses_package_without_designmap(tmp_path):
    bad = tmp_path / "bad.idml"
    _copy(_built(tmp_path), bad, lambda name, data: None if name == "designmap.xml" else data)
    errors = validate(bad, ROOT / "seed" / "schema")
    assert "designmap.xml: missing; InDesign will not open the package" in errors


def test_unreadable_designmap_is_reported_once(tmp_path):
    from test_build import break_member

    bad = tmp_path / "bad.idml"
    shutil.copy(ROOT / "seed" / "reference.idml", bad)
    break_member(bad, "designmap.xml", "crc")
    errors = validate(bad, ROOT / "seed" / "schema")
    assert len([line for line in errors if line.startswith("designmap.xml:")]) == 1
    assert not [line for line in errors if "<?aid?>" in line]


def test_designmap_io_error_is_reported_once(tmp_path, monkeypatch):
    real_read = zipfile.ZipFile.read

    def read(self, name, pwd=None):
        if name == "designmap.xml":
            raise OSError("disk read failed")
        return real_read(self, name, pwd)

    monkeypatch.setattr(zipfile.ZipFile, "read", read)
    errors = validate(ROOT / "seed" / "reference.idml", ROOT / "seed" / "schema")
    assert [line for line in errors if line.startswith("designmap.xml:")] == ["designmap.xml: disk read failed"]
