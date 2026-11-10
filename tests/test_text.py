from pathlib import Path

import pytest

from makeready.text import (
    FontMetrics,
    bottom_inset,
    content_height,
    first_baseline,
    read_metrics,
    tracking,
)

FONT = Path(__file__).parent / "fixtures" / "fonts" / "NimbusSans-Regular.otf"
NIMBUS = FontMetrics(ascender=729, descender=-271, units_per_em=1000)


def test_read_metrics():
    assert read_metrics(FONT) == NIMBUS


def test_tracking():
    assert tracking(0.6, 12) == pytest.approx(50.0)
    assert tracking(-0.24, 24) == pytest.approx(-10.0)
    assert tracking(0, 12) == 0


def test_content_height():
    assert content_height(NIMBUS, 12) == pytest.approx(12.0)


def test_first_baseline():
    assert first_baseline(NIMBUS, 12, 14.4) == pytest.approx(9.948)


def test_first_baseline_tight_leading():
    assert first_baseline(NIMBUS, 20, 18) == pytest.approx(13.58)


def test_bottom_inset():
    assert bottom_inset(NIMBUS, 12, 14.4) == pytest.approx(4.452)


def test_scaled_template():
    assert first_baseline(NIMBUS, 24 * 0.5, 28.8 * 0.5) == pytest.approx(9.948)
