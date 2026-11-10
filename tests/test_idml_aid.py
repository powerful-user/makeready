import re
import zipfile

import pytest
from fixture_repo import make_config, make_repo

from makeready.idml.document import compile_document
from makeready.spec import SpecError, load_template, resolve

AID = re.compile(rb"<\?aid\s[^>]*\?>")


def _compile(tmp_path, repo):
    resolved = resolve(repo, "spring", "garden")
    return compile_document(repo, make_config(tmp_path), resolved, load_template(repo, resolved.template))


def _seed_aid(repo):
    with zipfile.ZipFile(repo / "seed" / "reference.idml") as package:
        found = AID.search(package.read("designmap.xml"))
    assert found is not None
    return found.group(0)


def test_designmap_keeps_the_seed_aid_instruction(tmp_path):
    repo = make_repo(tmp_path, real_seed=True)
    lines = _compile(tmp_path, repo).parts["designmap.xml"].split(b"\n")
    assert lines[0].startswith(b"<?xml ")
    assert lines[1] == _seed_aid(repo)
    assert lines[2].startswith(b"<Document ")


def test_seed_without_aid_instruction_is_refused(tmp_path):
    repo = make_repo(tmp_path, real_seed=True)
    seed = repo / "seed" / "reference.idml"
    stripped = tmp_path / "stripped.idml"
    with zipfile.ZipFile(seed) as source, zipfile.ZipFile(stripped, "w") as package:
        for info in source.infolist():
            data = source.read(info)
            if info.filename == "designmap.xml":
                data = AID.sub(b"", data)
            package.writestr(info, data)
    stripped.replace(seed)
    with pytest.raises(SpecError, match=r"designmap\.xml has no <\?aid\?> instruction") as caught:
        _compile(tmp_path, repo)
    assert str(seed) in str(caught.value)


def test_aid_instruction_is_read_from_the_prolog_only():
    from makeready.idml.package import aid_instruction

    aid = b'<?aid style="50" type="document" readerVersion="6.0" featureSet="257" product="21.6(57)" ?>'
    decl = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    assert aid_instruction(decl + b"\n" + aid + b"\n<Document/>\n") == aid
    assert aid_instruction(aid + b"\n<Document/>\n") == aid
    assert aid_instruction(decl + b"\n<Document/>\n" + aid + b"\n") is None
    assert aid_instruction(b"\xef\xbb\xbf" + decl + b"\n" + aid + b"\n<Document/>\n") is None
    assert aid_instruction(decl + b"\n<Document/>\n") is None
    assert aid_instruction(decl + b"\r\n\t " + aid + b"\n<Document/>\n") == aid
    assert aid_instruction(decl + b"\n<?aidx style=\"50\" ?>\n<Document/>\n") is None
    first = b'<?aid style="50" note="a?b" ?>'
    assert aid_instruction(decl + b"\n" + first + b"<?other x ?>\n<Document/>\n") == first


def test_designmap_keeps_a_changed_seed_instruction(tmp_path):
    repo = make_repo(tmp_path, real_seed=True)
    seed = repo / "seed" / "reference.idml"
    changed = b'<?aid style="50" type="document" readerVersion="6.0" featureSet="257" product="99.9(1)" ?>'
    rewritten = tmp_path / "rewritten.idml"
    with zipfile.ZipFile(seed) as source, zipfile.ZipFile(rewritten, "w") as package:
        for info in source.infolist():
            data = source.read(info)
            if info.filename == "designmap.xml":
                data = AID.sub(changed, data)
            package.writestr(info, data)
    rewritten.replace(seed)
    lines = _compile(tmp_path, repo).parts["designmap.xml"].split(b"\n")
    assert lines[1] == changed
