import struct
import zipfile
from pathlib import Path

import pytest

from makeready.idml.package import read_part, write_package


def make_seed(path):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.adobe.indesign-idml-package")
        z.writestr("designmap.xml", "<Document/>")
        z.writestr("Resources/Graphic.xml", "<Graphic/>")
        z.writestr("Spreads/Spread_old.xml", "<Spread/>")
        z.writestr("Stories/Story_old.xml", "<Story/>")
        z.writestr("META-INF/container.xml", "<container/>")
    return path


def test_mimetype_first_and_stored(tmp_path):
    out = tmp_path / "out.idml"
    write_package(make_seed(tmp_path / "seed.idml"), out, {"Spreads/Spread_u1.xml": b"<Spread/>"})
    with zipfile.ZipFile(out) as z:
        first = z.infolist()[0]
        assert first.filename == "mimetype"
        assert first.compress_type == zipfile.ZIP_STORED
        assert z.read("mimetype") == b"application/vnd.adobe.indesign-idml-package"
        assert all(i.compress_type == zipfile.ZIP_DEFLATED for i in z.infolist()[1:])


def test_parts_replace_and_drop(tmp_path):
    out = tmp_path / "out.idml"
    parts = {"designmap.xml": b"<Document new='1'/>", "Spreads/Spread_u1.xml": b"<Spread/>"}
    write_package(make_seed(tmp_path / "seed.idml"), out, parts)
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    assert "Spreads/Spread_old.xml" not in names
    assert "Stories/Story_old.xml" not in names
    assert "META-INF/container.xml" in names
    assert "Resources/Graphic.xml" in names
    assert names.count("designmap.xml") == 1
    assert read_part(out, "designmap.xml") == b"<Document new='1'/>"


def test_read_part_missing(tmp_path):
    with pytest.raises(KeyError):
        read_part(make_seed(tmp_path / "s.idml"), "nope.xml")


def break_member(path, name, how):
    blob = bytearray(Path(path).read_bytes())
    with zipfile.ZipFile(path) as package:
        info = package.getinfo(name)
    if how == "crc":
        struct.pack_into("<I", blob, info.header_offset + 14, 0)
        encoded = name.encode("utf-8")
        signature = b"PK\x01\x02"
        index = 0
        while True:
            found = blob.find(signature, index)
            assert found >= 0
            length = struct.unpack_from("<H", blob, found + 28)[0]
            if bytes(blob[found + 46:found + 46 + length]) == encoded:
                struct.pack_into("<I", blob, found + 16, 0)
                break
            index = found + 4
    else:
        name_len, extra_len = struct.unpack_from("<HH", blob, info.header_offset + 26)
        start = info.header_offset + 30 + name_len + extra_len
        blob[start] ^= 0xFF
    Path(path).write_bytes(blob)


def named_read_error(fn, path, name):
    try:
        fn()
    except Exception as error:
        text = str(error)
    else:
        pytest.fail("read succeeded")
    assert str(path) in text
    assert name in text


def test_read_part_bad_crc_names_package_and_member(tmp_path):
    path = make_seed(tmp_path / "seed.idml")
    break_member(path, "designmap.xml", "crc")
    named_read_error(lambda: read_part(path, "designmap.xml"), path, "designmap.xml")


def test_read_part_bad_deflate_names_package_and_member(tmp_path):
    path = make_seed(tmp_path / "seed.idml")
    break_member(path, "designmap.xml", "deflate")
    named_read_error(lambda: read_part(path, "designmap.xml"), path, "designmap.xml")


def test_write_package_bad_crc_names_package_and_member(tmp_path):
    seed = make_seed(tmp_path / "seed.idml")
    break_member(seed, "designmap.xml", "crc")
    named_read_error(lambda: write_package(seed, tmp_path / "out.idml", {}), seed, "designmap.xml")


def test_write_package_bad_deflate_names_package_and_member(tmp_path):
    seed = make_seed(tmp_path / "seed.idml")
    break_member(seed, "designmap.xml", "deflate")
    named_read_error(lambda: write_package(seed, tmp_path / "out.idml", {}), seed, "designmap.xml")


def test_write_package_leaves_preexisting_temp(tmp_path):
    out = tmp_path / "out.idml"
    temporary = tmp_path / "out.idml.tmp"
    temporary.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        write_package(make_seed(tmp_path / "seed.idml"), out, {})
    assert temporary.read_bytes() == b"keep"
    assert not out.exists()


def short_read(monkeypatch, name):
    original = zipfile.ZipFile.read

    def read(self, member, pwd=None):
        if getattr(member, "filename", member) == name:
            raise EOFError
        return original(self, member, pwd)

    monkeypatch.setattr(zipfile.ZipFile, "read", read)


def test_read_part_short_member_names_package_and_member(tmp_path, monkeypatch):
    path = make_seed(tmp_path / "seed.idml")
    short_read(monkeypatch, "designmap.xml")
    named_read_error(lambda: read_part(path, "designmap.xml"), path, "designmap.xml")
    with pytest.raises(zipfile.BadZipFile, match="member data ends early"):
        read_part(path, "designmap.xml")


def test_write_package_short_member_names_package_and_member(tmp_path, monkeypatch):
    seed = make_seed(tmp_path / "seed.idml")
    short_read(monkeypatch, "designmap.xml")
    named_read_error(lambda: write_package(seed, tmp_path / "out.idml", {}), seed, "designmap.xml")
