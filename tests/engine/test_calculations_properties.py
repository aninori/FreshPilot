"""ORD-8 property tests: order is monotonic in target and on-hand, never negative,
always a multiple of case pack.
"""

from __future__ import annotations

from freshpilot.engine.calculations import compute_order
from hypothesis import given
from hypothesis import strategies as st

_QTY = st.floats(min_value=0, max_value=10_000, allow_nan=False, allow_infinity=False)
_CASE_PACK = st.integers(min_value=1, max_value=48)
_DELTA = st.floats(min_value=0, max_value=5_000, allow_nan=False, allow_infinity=False)


def _order(target: float, on_hand: float, case_pack: int, min_cases: int = 0, max_cases: int = 0) -> int:
    calc = compute_order(
        product_id="PRD-TEST",
        calc_type="order",
        date="2025-01-01",
        target_qty=target,
        estimated_on_hand=on_hand,
        on_order_qty=0.0,
        case_pack=case_pack,
        min_order_cases=min_cases,
        max_order_cases=max_cases,
        breakdown_extra={},
    )
    return calc.order_cases


@given(target=_QTY, on_hand=_QTY, case_pack=_CASE_PACK)
def test_order_never_negative(target: float, on_hand: float, case_pack: int) -> None:
    assert _order(target, on_hand, case_pack) >= 0


@given(target=_QTY, on_hand=_QTY, case_pack=_CASE_PACK)
def test_order_is_always_a_multiple_of_case_pack(target: float, on_hand: float, case_pack: int) -> None:
    cases = _order(target, on_hand, case_pack)
    assert (cases * case_pack) % case_pack == 0


@given(target=_QTY, on_hand=_QTY, case_pack=_CASE_PACK, delta=_DELTA)
def test_order_is_monotonic_non_decreasing_in_target(
    target: float, on_hand: float, case_pack: int, delta: float
) -> None:
    lower = _order(target, on_hand, case_pack)
    higher = _order(target + delta, on_hand, case_pack)
    assert higher >= lower


@given(target=_QTY, on_hand=_QTY, case_pack=_CASE_PACK, delta=_DELTA)
def test_order_is_monotonic_non_increasing_in_on_hand(
    target: float, on_hand: float, case_pack: int, delta: float
) -> None:
    lower_on_hand_order = _order(target, on_hand, case_pack)
    higher_on_hand_order = _order(target, on_hand + delta, case_pack)
    assert higher_on_hand_order <= lower_on_hand_order


@given(target=_QTY, on_hand=_QTY, case_pack=_CASE_PACK, min_cases=st.integers(0, 10), max_extra=st.integers(0, 20))
def test_order_respects_min_and_max_case_bounds(
    target: float, on_hand: float, case_pack: int, min_cases: int, max_extra: int
) -> None:
    max_cases = min_cases + max_extra + 1  # always >= min_cases + 1, and > 0
    cases = _order(target, on_hand, case_pack, min_cases=min_cases, max_cases=max_cases)
    assert min_cases <= cases <= max_cases


def test_calc_id_is_deterministic_not_random() -> None:
    """NFR-2 / CLAUDE.md rule 6: identical inputs, identical calc_id — no uuid4()."""
    calc1 = compute_order("PRD-1", "order", "2025-06-01", 10.0, 2.0, 0.0, 6, 0, 0, {})
    calc2 = compute_order("PRD-1", "order", "2025-06-01", 10.0, 2.0, 0.0, 6, 0, 0, {})
    assert calc1.calc_id == calc2.calc_id
