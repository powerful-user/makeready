import pytest

from makeready.crop import Crop, crop


def test_fill_landscape_centre():
    assert crop(100, 100, 200, 100, "fill") == Crop(scale=1.0, x=-50.0, y=0.0)


def test_fill_focus_edges():
    assert crop(100, 100, 200, 100, "fill", (0, 0.5)).x == 0
    assert crop(100, 100, 200, 100, "fill", (1, 0.5)).x == -100


def test_fill_scales_up():
    c = crop(300, 100, 100, 100, "fill", (0.5, 0.35))
    assert c.scale == 3
    assert c.x == 0
    assert c.y == pytest.approx(0.35 * (100 - 300))


def test_fit():
    assert crop(100, 100, 200, 100, "fit") == Crop(scale=0.5, x=0.0, y=25.0)


def test_none_smaller():
    assert crop(100, 100, 50, 50, "none", (0, 1)) == Crop(scale=1.0, x=0.0, y=50.0)


def test_none_larger():
    assert crop(100, 100, 300, 100, "none", (0.5, 0.5)) == Crop(scale=1.0, x=-100.0, y=0.0)


@pytest.mark.parametrize(
    "args",
    [
        (100, 100, 200, 100, "stretch", (0.5, 0.5)),
        (100, 100, 200, 100, "fill", (1.2, 0.5)),
        (100, 100, 0, 100, "fill", (0.5, 0.5)),
        (0, 100, 200, 100, "fit", (0.5, 0.5)),
    ],
)
def test_invalid(args):
    with pytest.raises(ValueError):
        crop(*args)
