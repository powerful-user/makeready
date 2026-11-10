import pytest

from makeready.geometry import (
    IDENTITY,
    apply,
    bbox,
    compose,
    corners,
    from_figma,
    rotation_deg,
    scaled,
    to_spread,
    translate,
)

ROT90 = from_figma([[0, -1, 50], [1, 0, 0]])


def close(a, b):
    return all(abs(x - y) < 1e-9 for x, y in zip(a, b, strict=True))


def test_from_figma():
    assert from_figma([[1, 0, 10], [0, 1, 20]]) == (1, 0, 0, 1, 10, 20)
    assert IDENTITY == (1, 0, 0, 1, 0, 0)


def test_apply_translate():
    assert apply(translate(5, 7), 1, 2) == (6, 9)


def test_compose_order():
    rotate = from_figma([[0, -1, 0], [1, 0, 0]])
    assert close(apply(compose(translate(10, 0), rotate), 1, 0), (10, 1))
    assert compose(translate(1, 2), translate(3, 4)) == translate(4, 6)


def test_corners_and_bbox():
    pts = corners(ROT90, 20, 10)
    assert close(pts[0], (50, 0))
    assert close(pts[1], (50, 20))
    assert close(pts[2], (40, 20))
    assert close(pts[3], (40, 0))
    assert close(bbox(pts), (40, 0, 50, 20))


def test_rotation_deg():
    assert rotation_deg(IDENTITY) == 0
    assert rotation_deg(ROT90) == pytest.approx(90)
    assert rotation_deg(from_figma([[0.8660254, 0.5, 0], [-0.5, 0.8660254, 0]])) == pytest.approx(-30)


def test_scaled_keeps_linear_part():
    m = scaled(ROT90, 2)
    assert m == (0, 1, -1, 0, 100, 0)
    assert close(corners(m, 40, 20)[2], (80, 40))


def test_to_spread():
    assert to_spread(0, 0, 612, 792) == (-306, -396)
    assert to_spread(300, 100, 612, 792) == (-6, -296)
