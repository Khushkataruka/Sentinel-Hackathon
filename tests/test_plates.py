"""Confusion-weighted plate matching.

The property that matters: a misread plate stays close, a different plate
does not, even when both differ by one character.
"""

from __future__ import annotations

from sentinel.correlation import plates


def test_identical_plates_are_identical():
    assert plates.similarity("GJ01AB1234", "GJ01AB1234") == 1.0


def test_confusable_substitution_is_cheap():
    """0 for D is the mistake OCR actually makes."""
    assert plates.similarity("GJ01DB1234", "GJ01OB1234") > 0.95


def test_a_genuinely_different_plate_is_further_than_a_misread_one():
    """The whole point. Both are one character apart from the target; only
    one of them is the same vehicle."""
    target = "GJ01DB1234"
    misread = plates.similarity(target, "GJ010B1234")   # D -> 0, confusable
    different = plates.similarity(target, "GJ01DB1235")  # 4 -> 5, not confusable
    assert misread > different


def test_matching_respects_the_threshold():
    assert plates.matches("GJ05BS4471", "GJ05B54471")        # S -> 5
    assert not plates.matches("GJ05BS4471", "MH12XY9988")


def test_normalisation_strips_separators():
    assert plates.normalise("gj-01 ab 1234") == "GJ01AB1234"
    assert plates.similarity("GJ 01 AB 1234", "GJ01AB1234") == 1.0


def test_confusions_are_symmetric():
    a = plates.similarity("GJ01B1234A", "GJ0181234A")
    b = plates.similarity("GJ0181234A", "GJ01B1234A")
    assert abs(a - b) < 1e-9


def test_prefilter_anchors_on_the_characters_ocr_gets_right():
    pattern = plates.trigram_prefilter("GJ01AB1234")
    assert pattern.startswith("%GJ%")
    assert pattern.endswith("1234%")
