"""GRD-3 faithfulness tests, including CLAUDE.md's named tricky cases:
"6 cases (48 lb)", "15.2 lb/day", "< 0.1%", "-18 to -24%".
"""

from __future__ import annotations

from freshpilot.guardrails.faithfulness import extract_numbers, is_faithful


def test_extract_numbers_from_case_and_weight() -> None:
    numbers = [v for v, _ in extract_numbers("6 cases (48 lb)")]
    assert numbers == [6.0, 48.0]


def test_extract_numbers_from_rate() -> None:
    numbers = [v for v, _ in extract_numbers("15.2 lb/day")]
    assert numbers == [15.2]


def test_extract_numbers_from_small_percent() -> None:
    numbers = extract_numbers("< 0.1%")
    assert numbers == [(0.1, True)]


def test_extract_numbers_handles_unicode_minus_range() -> None:
    numbers = [v for v, _ in extract_numbers("−18 to −24%")]
    assert numbers == [-18.0, -24.0]


def test_is_faithful_matches_exact_values() -> None:
    faithful, unmatched = is_faithful("6 cases (48 lb)", known_values=[6.0, 48.0])
    assert faithful
    assert unmatched == []


def test_is_faithful_percent_matches_fraction_form() -> None:
    """"< 0.1%" should match a known value stored as the fraction 0.001 (INV-3's threshold)."""
    faithful, _ = is_faithful("probability was under 0.1%", known_values=[0.001])
    assert faithful


def test_is_faithful_rejects_unmatched_number() -> None:
    faithful, unmatched = is_faithful("ordered 99 units", known_values=[6.0, 48.0])
    assert not faithful
    assert unmatched == [99.0]


def test_is_faithful_within_tolerance() -> None:
    """0.5% tolerance: 48.2 should still match a known value of 48.0."""
    faithful, _ = is_faithful("48.2 lb on hand", known_values=[48.0], tolerance=0.005)
    assert faithful


def test_is_faithful_outside_tolerance() -> None:
    faithful, _ = is_faithful("50 lb on hand", known_values=[48.0], tolerance=0.005)
    assert not faithful


def test_GRD3_thousands_and_exponents_cannot_disguise_invented_quantities():
    assert extract_numbers("1,000 cases") == [(1000.0, False)]
    assert not is_faithful("1,000 cases", [1.0, 0.0])[0]
    assert extract_numbers("1e6 units") == [(1000000.0, False)]
    assert not is_faithful("1e6 units", [1.0, 6.0])[0]
    assert extract_numbers(".5 lb") == [(0.5, False)]


def test_GRD3_written_numbers_are_checked():
    assert extract_numbers("twenty-four cases and three weeks") == [(24.0, False), (3.0, False)]
    assert extract_numbers("one hundred and twenty units") == [(120.0, False)]
    assert not is_faithful("three weeks", [22.0])[0]
    assert is_faithful("twenty-two days", [22.0])[0]
