"""CAT-1 unit-parser tests, including the two worked examples quoted in BRD §9.1."""

from __future__ import annotations

from freshpilot.catalog.units import parse_pack


def test_brd_example_case_weight_to_total_lb() -> None:
    """"CS/12 5LB" -> 60 lb."""
    parsed = parse_pack("LB", "CS/12 5LB")
    assert parsed.total_qty == 60.0
    assert parsed.canonical_unit == "lb"
    assert parsed.unit_compatible


def test_brd_example_count_to_ea() -> None:
    """"12CT" -> 12 ea."""
    parsed = parse_pack("EA", "12CT")
    assert parsed.total_qty == 12.0
    assert parsed.canonical_unit == "ea"
    assert parsed.unit_compatible


def test_ct_and_ea_are_the_same_canonical_unit() -> None:
    parsed = parse_pack("CT", "12CT")
    assert parsed.canonical_unit == "ea"
    assert parsed.unit_compatible


def test_weight_only_pack_with_no_case_prefix() -> None:
    parsed = parse_pack("lb", "0.98 LB")
    assert parsed.total_qty == 0.98
    assert parsed.canonical_unit == "lb"
    assert parsed.unit_compatible


def test_case_only_pack_falls_back_to_case_count() -> None:
    parsed = parse_pack("LB", "CS/12")
    assert parsed.total_qty == 12.0
    assert parsed.canonical_unit == "lb"
    assert parsed.unit_compatible  # nothing in raw_pack to contradict the stated unit


def test_oz_converts_to_lb_via_pint() -> None:
    parsed = parse_pack("LB", "CS/6 8OZ")
    assert parsed.canonical_unit == "lb"
    assert parsed.total_qty is not None
    assert abs(parsed.total_qty - 3.0) < 1e-9  # 6 cases x 8oz = 48oz = 3lb


def test_unit_mixup_is_flagged_incompatible() -> None:
    """A count-style raw_unit paired with a weight-style raw_pack (as planted by
    plant_unit_mixups in the M1 generator) must be caught as incompatible."""
    parsed = parse_pack("CT", "CS/12 5LB")
    assert not parsed.unit_compatible


def test_unparseable_pack_returns_none_quantity() -> None:
    parsed = parse_pack("LB", "some garbage text")
    assert parsed.total_qty is None
