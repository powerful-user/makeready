import errno
import json
import os
import shutil
import struct
import subprocess
import zipfile
from pathlib import Path

import pytest
from fixture_repo import ROOT, make_config, make_repo, write_config

from makeready.build import BuildFailed, _copy_file, _copy_files, _point_latest, _write_text, build
from makeready.cli import main
from makeready.idml.validate import validate
from makeready.spec import SpecError, set_status


def setup(tmp_path):
    return make_repo(tmp_path, real_seed=True), make_config(tmp_path)


def test_build_layout(tmp_path):
    repo, cfg = setup(tmp_path)
    res = build(repo, cfg, "spring", "garden")
    d = repo / "build" / "spring" / "garden" / res.build_id
    assert res.path == d
    for name in ["garden.idml", "manifest.json", "checks.json", "geometry.json", "links/kitchen.jpg",
                 "links/logo.pdf", "Document Fonts/NimbusSans-Regular.otf", "Document Fonts/NimbusSans-Bold.otf"]:
        assert (d / name).exists(), name
    assert os.readlink(repo / "build/spring/garden/latest") == res.build_id
    assert json.loads((d / "manifest.json").read_text())["photos/kitchen.jpg"]


def test_build_is_reused(tmp_path):
    repo, cfg = setup(tmp_path)
    a = build(repo, cfg, "spring", "garden")
    mtime = (a.path / "garden.idml").stat().st_mtime_ns
    b = build(repo, cfg, "spring", "garden")
    assert a.build_id == b.build_id
    assert (b.path / "garden.idml").stat().st_mtime_ns == mtime


def test_new_inputs_new_build(tmp_path):
    repo, cfg = setup(tmp_path)
    a = build(repo, cfg, "spring", "garden")
    (repo / "series/spring/garden.yaml").write_text("overrides:\n  headline: {tracking: -5}\n")
    b = build(repo, cfg, "spring", "garden")
    assert a.build_id != b.build_id
    assert a.path.exists()
    assert os.readlink(repo / "build/spring/garden/latest") == b.build_id


def test_failed_checks_leave_nothing(tmp_path):
    repo, cfg = setup(tmp_path)
    (repo / "brand/assets/logo.pdf").unlink()
    with pytest.raises(BuildFailed) as err:
        build(repo, cfg, "spring", "garden")
    assert err.value.findings[0].code == "missing-file"
    folder = repo / "build" / "spring" / "garden"
    assert not folder.exists() or not any(folder.iterdir())


def test_approved_refused(tmp_path):
    repo, cfg = setup(tmp_path)
    set_status(repo, "spring", "garden", "approved")
    with pytest.raises(SpecError, match="approved"):
        build(repo, cfg, "spring", "garden")
    assert build(repo, cfg, "spring", "garden", reopen=True).build_id


def test_schema_valid(tmp_path):
    repo, cfg = setup(tmp_path)
    res = build(repo, cfg, "spring", "garden")
    assert validate(res.path / "garden.idml", ROOT / "seed" / "schema") == []


def test_schema_catches_bogus(tmp_path):
    repo, cfg = setup(tmp_path)
    res = build(repo, cfg, "spring", "garden")
    bad = tmp_path / "bad.idml"
    with zipfile.ZipFile(res.path / "garden.idml") as src, zipfile.ZipFile(bad, "w") as dst:
        for info in src.infolist():
            data = src.read(info)
            if info.filename.startswith("Spreads/"):
                data = data.replace(b"</Spread>", b"<Bogus/></Spread>")
            dst.writestr(info, data)
    errors = validate(bad, ROOT / "seed" / "schema")
    assert errors
    assert any("Spreads/" in e for e in errors)


def test_cli_build_series(tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path, real_seed=True)
    monkeypatch.setenv("MAKEREADY_CONFIG", str(write_config(tmp_path)))
    assert main(["--repo", str(repo), "build", "spring"]) == 0
    out = capsys.readouterr().out
    assert "spring/garden" in out
    assert "spring/open-house" in out


def expect_build_failed(fn):
    try:
        fn()
    except BuildFailed as error:
        return error
    except Exception as error:
        pytest.fail(f"{type(error).__name__}: {error}")
    pytest.fail("build succeeded")


def run_cli(argv):
    try:
        return main(argv)
    except Exception as error:
        pytest.fail(f"{type(error).__name__}: {error}")


def assert_no_temps(repo):
    root = repo / "build"
    if not root.exists():
        return
    left = [path for path in root.rglob("*") if ".tmp" in path.name]
    assert left == []


def _dir_publish(src, dst) -> bool:
    if src.is_symlink() or not src.is_dir():
        return False
    if len(dst.name) != 16 or any(char not in "0123456789abcdef" for char in dst.name):
        return False
    return src.name.startswith(f".{dst.name}.tmp-")


def hide_jing(monkeypatch):
    original = subprocess.run

    def run(command, *args, **kwargs):
        if isinstance(command, list) and command and command[0] == "jing":
            raise FileNotFoundError(command[0])
        return original(command, *args, **kwargs)

    monkeypatch.setattr("makeready.idml.validate.subprocess.run", run)


def test_io_missing_jing_raises_build_failed(tmp_path, monkeypatch):
    repo, cfg = setup(tmp_path)
    hide_jing(monkeypatch)
    error = expect_build_failed(lambda: build(repo, cfg, "spring", "garden"))
    assert error.errors == ["install jing: sudo apt install jing"]
    assert_no_temps(repo)
    folder = repo / "build" / "spring" / "garden"
    assert not folder.exists() or not any(path.is_dir() and len(path.name) == 16 for path in folder.iterdir())


def test_io_missing_jing_prints_and_continues(tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path, real_seed=True)
    monkeypatch.setenv("MAKEREADY_CONFIG", str(write_config(tmp_path)))
    hide_jing(monkeypatch)
    code = run_cli(["--repo", str(repo), "build", "spring"])
    captured = capsys.readouterr()
    assert code == 1
    assert "Traceback" not in captured.err
    assert captured.err.count("install jing: sudo apt install jing") == 2
    assert "spring/garden" in captured.err
    assert "spring/open-house" in captured.err
    assert_no_temps(repo)


def test_io_temp_dir_error_names_the_path(tmp_path, monkeypatch):
    repo, cfg = setup(tmp_path)
    real_mkdir = Path.mkdir

    def mkdir(self, mode=0o777, parents=False, exist_ok=False):
        if self.name.startswith(".") and ".tmp-" in self.name:
            raise OSError(errno.EIO, "Input/output error", str(self))
        return real_mkdir(self, mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    error = expect_build_failed(lambda: build(repo, cfg, "spring", "garden"))
    assert any(".tmp-" in line and "Input/output error" in line for line in error.errors)
    assert_no_temps(repo)


def test_io_parent_dir_error_names_the_path(tmp_path, monkeypatch):
    repo, cfg = setup(tmp_path)
    folder = repo / "build" / "spring" / "garden"
    real_mkdir = Path.mkdir

    def mkdir(self, mode=0o777, parents=False, exist_ok=False):
        if parents and self == folder:
            raise OSError(errno.EACCES, "Permission denied", str(self))
        return real_mkdir(self, mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    error = expect_build_failed(lambda: build(repo, cfg, "spring", "garden"))
    assert any(str(folder) in line and "Permission denied" in line for line in error.errors)
    assert_no_temps(repo)


def test_io_link_dir_error_names_the_path(tmp_path, monkeypatch):
    folder = tmp_path / "links"
    real_mkdir = Path.mkdir

    def mkdir(self, mode=0o777, parents=False, exist_ok=False):
        if self == folder:
            raise OSError(errno.EACCES, "Permission denied", str(self))
        return real_mkdir(self, mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    error = expect_build_failed(lambda: _copy_files(folder, [], "links", []))
    assert any(str(folder) in line and "Permission denied" in line for line in error.errors)


def test_io_copy_stat_error_names_the_source(tmp_path, monkeypatch):
    source = tmp_path / "font.otf"
    source.write_bytes(b"otf")
    folder = tmp_path / "Document Fonts"
    real_is_file = Path.is_file

    def is_file(self):
        if self == source.resolve():
            raise OSError(errno.EIO, "Input/output error", str(self))
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", is_file)
    error = expect_build_failed(lambda: _copy_files(folder, [("font.otf", source)], "Document Fonts", []))
    assert any(str(source) in line and "Input/output error" in line for line in error.errors)


@pytest.mark.parametrize("filename", ["manifest.json", "checks.json", "geometry.json"])
def test_io_text_write_names_the_path(tmp_path, monkeypatch, filename):
    repo, cfg = setup(tmp_path)
    real_write_text = Path.write_text

    def write_text(self, data, encoding=None, errors=None, newline=None):
        if self.name.startswith(filename + ".tmp-"):
            raise OSError(errno.ENOSPC, "No space left on device", str(self))
        return real_write_text(self, data, encoding=encoding, errors=errors, newline=newline)

    monkeypatch.setattr(Path, "write_text", write_text)
    error = expect_build_failed(lambda: build(repo, cfg, "spring", "garden"))
    assert any(line.split(":")[0].endswith(filename) and "No space left on device" in line for line in error.errors)
    assert_no_temps(repo)


def test_io_write_text_cleanup_keeps_the_write_error(tmp_path, monkeypatch):
    path = tmp_path / "manifest.json"
    real_write_text = Path.write_text
    real_unlink = Path.unlink

    def write_text(self, data, encoding=None, errors=None, newline=None):
        if ".tmp-" in self.name:
            raise OSError(errno.ENOSPC, "No space left on device", str(self))
        return real_write_text(self, data, encoding=encoding, errors=errors, newline=newline)

    def unlink(self, missing_ok=False):
        if ".tmp-" in self.name:
            raise OSError(errno.EIO, "Input/output error", str(self))
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "write_text", write_text)
    monkeypatch.setattr(Path, "unlink", unlink)
    with pytest.raises(OSError) as err:
        _write_text(path, "{}")
    assert err.value.errno == errno.ENOSPC


def test_io_rename_error_names_the_destination(tmp_path, monkeypatch):
    repo, cfg = setup(tmp_path)
    original = os.replace

    def replace(src, dst):
        src_p, dst_p = Path(src), Path(dst)
        if _dir_publish(src_p, dst_p):
            raise OSError(errno.EACCES, "Permission denied", str(dst_p))
        return original(src, dst)

    monkeypatch.setattr("makeready.build.os.replace", replace)
    error = expect_build_failed(lambda: build(repo, cfg, "spring", "garden"))
    matched = [line for line in error.errors if "Permission denied" in line]
    assert matched
    assert all(".tmp-" not in line and "build/spring/garden/" in line for line in matched)
    assert_no_temps(repo)


def test_io_rename_race_reuses_published_build(tmp_path, monkeypatch):
    repo, cfg = setup(tmp_path)
    original = os.replace

    def replace(src, dst):
        src_p, dst_p = Path(src), Path(dst)
        if _dir_publish(src_p, dst_p):
            dst_p.mkdir(parents=True, exist_ok=True)
            (dst_p / "garden.idml").write_bytes(b"other-build")
            raise OSError(errno.ENOTEMPTY, "Directory not empty", str(dst_p))
        return original(src, dst)

    monkeypatch.setattr("makeready.build.os.replace", replace)
    result = build(repo, cfg, "spring", "garden")
    assert (result.path / "garden.idml").read_bytes() == b"other-build"
    assert os.readlink(repo / "build/spring/garden/latest") == result.build_id
    assert_no_temps(repo)


def test_io_compile_error_names_the_path(tmp_path, monkeypatch):
    repo, cfg = setup(tmp_path)
    seed = repo / "seed" / "reference.idml"

    def boom(*args, **kwargs):
        raise OSError(errno.EIO, "Input/output error", str(seed))

    monkeypatch.setattr("makeready.build.compile_document", boom)
    error = expect_build_failed(lambda: build(repo, cfg, "spring", "garden"))
    assert any(str(seed) in line and "Input/output error" in line for line in error.errors)
    assert_no_temps(repo)


def test_io_idml_stat_error_names_the_path(tmp_path, monkeypatch):
    repo, cfg = setup(tmp_path)
    real_is_file = Path.is_file

    def is_file(self):
        if self.name == "garden.idml" and len(self.parent.name) == 16:
            raise OSError(errno.EIO, "Input/output error", str(self))
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", is_file)
    error = expect_build_failed(lambda: build(repo, cfg, "spring", "garden"))
    assert any("garden.idml" in line and "Input/output error" in line for line in error.errors)


def test_io_dest_stat_error_names_the_path(tmp_path, monkeypatch):
    repo, cfg = setup(tmp_path)
    real_exists = Path.exists

    def exists(self):
        if self.parent.name == "garden" and len(self.name) == 16 and all(c in "0123456789abcdef" for c in self.name):
            raise OSError(errno.EIO, "Input/output error", str(self))
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", exists)
    error = expect_build_failed(lambda: build(repo, cfg, "spring", "garden"))
    assert any("Input/output error" in line and "build/spring/garden/" in line for line in error.errors)


def test_io_temp_stat_error_names_the_path(tmp_path, monkeypatch):
    repo, cfg = setup(tmp_path)
    real_exists = Path.exists

    def exists(self):
        if self.name.startswith(".") and ".tmp-" in self.name:
            raise OSError(errno.EIO, "Input/output error", str(self))
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", exists)
    error = expect_build_failed(lambda: build(repo, cfg, "spring", "garden"))
    assert any(".tmp-" in line and "Input/output error" in line for line in error.errors)
    assert_no_temps(repo)


def test_io_series_continues_after_document_error(tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path, real_seed=True)
    monkeypatch.setenv("MAKEREADY_CONFIG", str(write_config(tmp_path)))
    real_mkdir = Path.mkdir

    def mkdir(self, mode=0o777, parents=False, exist_ok=False):
        if self.name.startswith(".") and ".tmp-" in self.name and self.parent.name == "garden":
            raise OSError(errno.EIO, "Input/output error", str(self))
        return real_mkdir(self, mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    code = run_cli(["--repo", str(repo), "build", "spring"])
    captured = capsys.readouterr()
    assert code == 1
    assert "Traceback" not in captured.err
    assert "spring/garden" in captured.err
    assert "Input/output error" in captured.err
    assert "spring/open-house" in captured.out
    assert_no_temps(repo)


def test_io_stray_oserror_does_not_stop_the_series(tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path, real_seed=True)
    monkeypatch.setenv("MAKEREADY_CONFIG", str(write_config(tmp_path)))
    real_build = build

    def fake_build(repo_path, cfg, series, doc, reopen=False):
        if doc == "garden":
            raise OSError(errno.EIO, "Input/output error", f"{repo_path}/build/{series}/{doc}")
        return real_build(repo_path, cfg, series, doc, reopen=reopen)

    monkeypatch.setattr("makeready.cli.build", fake_build)
    code = run_cli(["--repo", str(repo), "build", "spring"])
    captured = capsys.readouterr()
    assert code == 1
    assert "Traceback" not in captured.err
    assert "spring/garden" in captured.err
    assert "Input/output error" in captured.err
    assert "spring/open-house" in captured.out


def test_io_config_error_is_not_a_traceback(tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path, real_seed=True)
    config = write_config(tmp_path)
    monkeypatch.setenv("MAKEREADY_CONFIG", str(config))
    real_open = Path.open

    def open_(self, mode="r", *args, **kwargs):
        if self == config:
            raise OSError(errno.EACCES, "Permission denied", str(self))
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_)
    code = run_cli(["--repo", str(repo), "build", "spring"])
    captured = capsys.readouterr()
    assert code == 2
    assert "Traceback" not in captured.err
    assert "Permission denied" in captured.err
    assert str(config) in captured.err


def test_io_copy_cleanup_keeps_build_failed(tmp_path, monkeypatch):
    source = tmp_path / "a.bin"
    source.write_bytes(b"abc")
    dest = tmp_path / "out" / "a.bin"
    dest.parent.mkdir()

    def copyfile(src, dst, *args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device", str(src))

    def unlink(self, missing_ok=False):
        raise OSError(errno.EIO, "Input/output error", str(self))

    monkeypatch.setattr("makeready.build.shutil.copyfile", copyfile)
    monkeypatch.setattr(Path, "unlink", unlink)
    error = expect_build_failed(lambda: _copy_file(source, dest, []))
    assert any(str(source) in line and "No space left on device" in line for line in error.errors)


def test_preexisting_build_temp_is_left_in_place(tmp_path):
    repo, cfg = setup(tmp_path)
    first = build(repo, cfg, "spring", "garden")
    stale = first.path.parent / f".{first.build_id}.tmp-{os.getpid()}"
    shutil.rmtree(first.path)
    (first.path.parent / "latest").unlink()
    stale.mkdir()
    marker = stale / "keep.txt"
    marker.write_text("keep")
    error = expect_build_failed(lambda: build(repo, cfg, "spring", "garden"))
    assert any(str(stale) in line for line in error.errors)
    assert stale.is_dir()
    assert marker.read_text() == "keep"


def test_copy_file_leaves_preexisting_temp(tmp_path):
    source = tmp_path / "a.bin"
    source.write_bytes(b"new")
    dest = tmp_path / "out" / "a.bin"
    dest.parent.mkdir()
    temporary = dest.with_name(dest.name + f".tmp-{os.getpid()}")
    temporary.write_text("keep")
    error = expect_build_failed(lambda: _copy_file(source, dest, []))
    assert any(str(temporary) in line for line in error.errors)
    assert temporary.read_text() == "keep"
    assert not dest.exists()


def test_write_text_leaves_preexisting_temp(tmp_path):
    path = tmp_path / "manifest.json"
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text("keep")
    with pytest.raises(FileExistsError):
        _write_text(path, "new")
    assert temporary.read_text() == "keep"
    assert not path.exists()


def test_point_latest_leaves_preexisting_temp(tmp_path):
    folder = tmp_path / "doc"
    folder.mkdir()
    temporary = folder / f".latest.tmp-{os.getpid()}"
    temporary.write_text("keep")
    error = expect_build_failed(lambda: _point_latest(folder, "abcdef0123456789", []))
    assert any(str(temporary) in line for line in error.errors)
    assert temporary.read_text() == "keep"
    assert not (folder / "latest").exists()


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


def returned_errors(fn):
    try:
        return fn()
    except Exception as error:
        pytest.fail(f"{type(error).__name__}: {error}")


def test_validate_bad_crc_names_the_part(tmp_path):
    bad = tmp_path / "bad.idml"
    shutil.copy(ROOT / "seed" / "reference.idml", bad)
    break_member(bad, "designmap.xml", "crc")
    errors = returned_errors(lambda: validate(bad, ROOT / "seed" / "schema"))
    assert any(line.startswith("designmap.xml:") for line in errors)


def test_validate_bad_deflate_names_the_part(tmp_path):
    bad = tmp_path / "bad.idml"
    shutil.copy(ROOT / "seed" / "reference.idml", bad)
    break_member(bad, "designmap.xml", "deflate")
    errors = returned_errors(lambda: validate(bad, ROOT / "seed" / "schema"))
    assert any(line.startswith("designmap.xml:") for line in errors)


def test_io_latest_cleanup_keeps_build_failed(tmp_path, monkeypatch):
    folder = tmp_path / "doc"
    folder.mkdir()

    def replace(src, dst):
        raise OSError(errno.EACCES, "Permission denied", str(dst))

    def unlink(self, missing_ok=False):
        raise OSError(errno.EIO, "Input/output error", str(self))

    monkeypatch.setattr("makeready.build.os.replace", replace)
    monkeypatch.setattr(Path, "unlink", unlink)
    error = expect_build_failed(lambda: _point_latest(folder, "abcdef0123456789", []))
    assert any("latest" in line and "Permission denied" in line for line in error.errors)


def test_validate_short_member_names_the_part(tmp_path, monkeypatch):
    original = zipfile.ZipFile.read

    def read(self, member, pwd=None):
        if getattr(member, "filename", member) == "designmap.xml":
            raise EOFError
        return original(self, member, pwd)

    monkeypatch.setattr(zipfile.ZipFile, "read", read)
    errors = returned_errors(lambda: validate(ROOT / "seed" / "reference.idml", ROOT / "seed" / "schema"))
    assert any(line.startswith("designmap.xml:") for line in errors)
