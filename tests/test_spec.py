import pytest
import yaml
from fixture_repo import make_repo

from makeready.spec import SpecError, list_docs, load_template, resolve, set_status, slot_text, validate


def test_list_docs(tmp_path):
    assert list_docs(make_repo(tmp_path), "spring") == ["garden", "open-house"]


def test_resolve_variant(tmp_path):
    r = resolve(make_repo(tmp_path), "spring", "open-house")
    assert r.template == "flyer"
    assert r.slots["headline"] == "Open House, April 12"
    assert r.slots["body"] == "copy/body.md"
    assert r.slots["photo:hero"] == {"src": "kitchen.jpg", "fit": "fill", "focus": [0.5, 0.35]}
    assert r.overrides == {"Band": {"opacity": 0.9}}
    assert r.status == "draft"


def test_override_order(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "templates/flyer/template-overrides.yaml").write_text(
        "overrides:\n  headline: {size: 40, leading: 50}\n  Band: {opacity: 0.5}\n"
    )
    r = resolve(repo, "spring", "garden")
    assert r.overrides["headline"] == {"size": 54, "leading": 50, "tracking": -10}
    assert r.overrides["Band"] == {"opacity": 0.9}


def test_standalone_doc(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "series/spring/solo.yaml").write_text(
        "template: flyer\nslots:\n  headline: Solo\n  body: Inline body\n  photo:hero: {src: kitchen.jpg, fit: fit}\n"
    )
    assert "solo" in list_docs(repo, "spring")
    r = resolve(repo, "spring", "solo")
    assert r.slots["headline"] == "Solo"
    assert r.slots["body"] == "Inline body"


def test_unknown_doc(tmp_path):
    with pytest.raises(SpecError, match="no document spring/nope"):
        resolve(make_repo(tmp_path), "spring", "nope")


@pytest.mark.parametrize("series, doc", [("Spring", "garden"), ("spring", "a_b")])
def test_bad_names(tmp_path, series, doc):
    with pytest.raises(SpecError, match="lowercase"):
        resolve(make_repo(tmp_path), series, doc)


def test_validate_ok(tmp_path):
    repo = make_repo(tmp_path)
    validate(resolve(repo, "spring", "garden"), load_template(repo, "flyer"))


def test_validate_problems(tmp_path):
    repo = make_repo(tmp_path)
    r = resolve(repo, "spring", "garden")
    r.slots.pop("headline")
    r.slots["extra"] = "x"
    r.slots["photo:hero"] = {"src": "kitchen.jpg", "fit": "stretch", "focus": [1.5, 0]}
    r.overrides["Nope"] = {"x": 1}
    r.overrides["Band"] = {"colour": "red"}
    with pytest.raises(SpecError) as err:
        validate(r, load_template(repo, "flyer"))
    text = str(err.value)
    for needle in [
        "missing slot headline",
        "unknown slot extra",
        "photo:hero: fit must be fill, fit or none",
        "photo:hero: focus must be two numbers from 0 to 1",
        "unknown element Nope",
        "Band: unknown override colour",
    ]:
        assert needle in text


def test_slot_text(tmp_path):
    repo = make_repo(tmp_path)
    assert slot_text(repo, "spring", "copy/body.md").startswith("Join us **Saturday**")
    assert slot_text(repo, "spring", "Plain words") == "Plain words"


def test_set_status_creates_file(tmp_path):
    repo = make_repo(tmp_path)
    set_status(repo, "spring", "open-house", "approved")
    assert yaml.safe_load((repo / "series/spring/open-house.yaml").read_text()) == {"status": "approved"}
    assert resolve(repo, "spring", "open-house").status == "approved"


def test_set_status_keeps_fields(tmp_path):
    repo = make_repo(tmp_path)
    path = repo / "series/spring/garden.yaml"
    path.write_text("# Josh: tighter headline\n" + path.read_text())
    set_status(repo, "spring", "garden", "approved")
    text = path.read_text()
    assert text.startswith("# Josh: tighter headline\n")
    data = yaml.safe_load(text)
    assert data["status"] == "approved"
    assert data["overrides"] == {"headline": {"tracking": -10}}


def test_image_fit_must_be_text(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "series/spring/garden.yaml").write_text(
        "slots:\n  photo:hero: {src: kitchen.jpg, fit: []}\n"
    )
    with pytest.raises(SpecError, match="photo:hero: fit must be fill, fit or none"):
        validate(resolve(repo, "spring", "garden"), load_template(repo, "flyer"))


def test_set_status_rejects_unhashable(tmp_path):
    repo = make_repo(tmp_path)
    path = repo / "series/spring/garden.yaml"
    before = path.read_text()
    with pytest.raises(SpecError, match="status must be draft or approved") as err:
        set_status(repo, "spring", "garden", [])
    assert "[]" in str(err.value)
    assert path.read_text() == before


def test_set_status_flow_mapping(tmp_path):
    repo = make_repo(tmp_path)
    path = repo / "series/spring/garden.yaml"
    path.write_text("{overrides: {headline: {tracking: -10}}, status: draft}\n...\n")
    set_status(repo, "spring", "garden", "approved")
    data = yaml.safe_load(path.read_text())
    assert data == {"overrides": {"headline": {"tracking": -10}}, "status": "approved"}
