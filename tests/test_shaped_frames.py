"""Shaped frames (paths), exact image placement (place), non-printing shapes and
template hooks."""

import json

import pytest
from fixture_repo import make_config, make_repo
from test_compile import by_label, compiled, spread

from makeready.manifest import build_id, compute
from makeready.spec import SpecError, load_template, resolve

PANES = [
    [[0, 0], [0, 100], [200, 100], [200, 0]],
    [[300, 0], [300, 100], [500, 100], [500, 0]],
]
TRAPEZOID = [[0, 0], [200, 0], [200, 20], [0, 10]]

HOOKS = '''
import copy


def item(element, frame, context):
    if element["id"] != "Band":
        return None
    frame.set("Name", "hooked")
    extra = copy.deepcopy(frame)
    extra.set("Self", context.new_id())
    extra.find("Properties/Label/KeyValuePair").set("Value", "Band-copy")
    return [frame, extra]
'''


def edit_template(change):
    def edit(repo):
        path = repo / "templates" / "flyer" / "template.json"
        template = json.loads(path.read_text())
        change(template, {e["id"]: e for page in template["pages"] for e in page["elements"]})
        path.write_text(json.dumps(template))

    return edit


def anchors(item):
    return [
        [p.get("Anchor") for p in path.findall("PathPointArray/PathPointType")]
        for path in item.findall("Properties/PathGeometry/GeometryPathType")
    ]


def test_image_with_several_paths_is_one_compound_frame(tmp_path):
    edit = edit_template(lambda t, e: e["photo:hero"].update(paths=PANES))
    photo = by_label(spread(compiled(tmp_path, edit=edit)))["photo:hero"]
    assert photo.tag == "Polygon"
    assert anchors(photo) == [["0 0", "0 100", "200 100", "200 0"], ["300 0", "300 100", "500 100", "500 0"]]
    assert {g.get("PathOpen") for g in photo.iter("GeometryPathType")} == {"false"}
    assert photo.find("Image") is not None


def test_shape_with_one_path_is_a_polygon_and_can_be_nonprinting(tmp_path):
    edit = edit_template(lambda t, e: e["Band"].update(paths=[TRAPEZOID], nonprinting=True))
    band = by_label(spread(compiled(tmp_path, edit=edit)))["Band"]
    assert band.tag == "Polygon"
    assert anchors(band) == [["0 0", "200 0", "200 20", "0 10"]]
    assert band.get("Nonprinting") == "true"


def test_frames_without_paths_stay_rectangles_that_print(tmp_path):
    items = by_label(spread(compiled(tmp_path)))
    assert items["photo:hero"].tag == "Rectangle"
    assert items["Band"].tag == "Rectangle"
    assert items["Band"].get("Nonprinting") != "true"


def test_place_sets_the_image_transform_exactly(tmp_path):
    edit = edit_template(lambda t, e: e["photo:hero"].update(place=[0.5, 0, 0, 0.5, 10, -20]))
    image = by_label(spread(compiled(tmp_path, edit=edit)))["photo:hero"].find("Image")
    assert image.get("ItemTransform") == "0.5 0 0 0.5 10 -20"


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("paths", [[[0, 0], [1, 1]]], "paths"),
        ("paths", [], "paths"),
        ("paths", [[[0, 0], [1, "x"], [2, 2]]], "paths"),
        ("place", [1, 0, 0, 1, 0], "place"),
        ("nonprinting", "yes", "nonprinting"),
    ],
)
def test_bad_shape_fields_are_spec_errors(tmp_path, field, value, message):
    target = "Band" if field == "nonprinting" else "photo:hero"
    edit = edit_template(lambda t, e: e[target].update({field: value}))
    with pytest.raises(SpecError, match=message):
        compiled(tmp_path, edit=edit)


def with_hooks(source=HOOKS, name="hooks.py"):
    def edit(repo):
        edit_template(lambda t, e: t.update(hooks=name))(repo)
        (repo / "templates" / "flyer" / "hooks.py").write_text(source)

    return edit


def test_hook_can_change_a_frame_and_add_items(tmp_path):
    c = compiled(tmp_path, edit=with_hooks())
    items = by_label(spread(c))
    assert items["Band"].get("Name") == "hooked"
    assert "Band-copy" in items
    selves = [el.get("Self") for el in spread(c).iter() if el.get("Self")]
    assert len(selves) == len(set(selves))


WHERE = '''
def item(element, frame, context):
    if element["id"] == "Band":
        frame.set("Name", f"{context.series}/{context.doc}/{context.page}")
'''


def test_hook_is_told_the_series_document_and_page(tmp_path):
    items = by_label(spread(compiled(tmp_path, edit=with_hooks(WHERE))))
    assert items["Band"].get("Name") == "spring/garden/Flyer Front"


def test_hooks_must_be_a_python_file_beside_the_template(tmp_path):
    with pytest.raises(SpecError, match="hooks"):
        compiled(tmp_path, edit=with_hooks(name="../hooks.py"))


def test_hook_file_is_a_build_input(tmp_path):
    repo = make_repo(tmp_path, real_seed=True)
    with_hooks()(repo)
    cfg = make_config(tmp_path)

    def current():
        r = resolve(repo, "spring", "garden")
        return compute(repo, cfg, r, load_template(repo, r.template))

    first = current()
    assert "templates/flyer/hooks.py" in first
    (repo / "templates" / "flyer" / "hooks.py").write_text(HOOKS + "\n# changed\n")
    assert build_id(current()) != build_id(first)
