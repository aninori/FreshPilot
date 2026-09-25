"""Unit tests for EXC-1..6 detector logic on small synthetic inputs — the full
1280-product/28-day run lives in `make eval`, not the fast pytest suite."""

from __future__ import annotations

import pandas as pd
from freshpilot.exceptions.detectors import (
    detect_low_confidence_match,
    detect_order_swing,
    detect_plan_change,
    detect_shrink_spike,
    trailing_avg_receipts,
)
from freshpilot.exceptions.reasons import reason_for_item

DATE = pd.Timestamp("2025-06-30")


def test_shrink_spike_fires_when_7day_rate_exceeds_multiple() -> None:
    dates = pd.date_range(DATE - pd.Timedelta(days=27), DATE)
    waste = pd.DataFrame({"product_id": "P1", "date": dates, "qty_wasted": [1.0] * 21 + [10.0] * 7})
    inventory = pd.DataFrame({"product_id": "P1", "date": dates, "book_on_hand": [100.0] * 28})
    flag = detect_shrink_spike(waste, inventory, "P1", DATE)
    assert flag is not None
    assert flag.type == "shrink_spike"


def test_shrink_spike_silent_when_steady() -> None:
    dates = pd.date_range(DATE - pd.Timedelta(days=27), DATE)
    waste = pd.DataFrame({"product_id": "P1", "date": dates, "qty_wasted": [2.0] * 28})
    inventory = pd.DataFrame({"product_id": "P1", "date": dates, "book_on_hand": [100.0] * 28})
    assert detect_shrink_spike(waste, inventory, "P1", DATE) is None


def test_order_swing_requires_a_nonzero_order() -> None:
    assert detect_order_swing(50.0, "P1", DATE, order_units=0.0, has_known_driver=False) is None


def test_order_swing_silent_when_driver_explains_it() -> None:
    assert detect_order_swing(10.0, "P1", DATE, order_units=100.0, has_known_driver=True) is None


def test_order_swing_fires_past_threshold() -> None:
    flag = detect_order_swing(10.0, "P1", DATE, order_units=20.0, has_known_driver=False)
    assert flag is not None and flag.type == "order_swing"


def test_order_swing_silent_within_threshold() -> None:
    assert detect_order_swing(10.0, "P1", DATE, order_units=15.0, has_known_driver=False) is None


def test_low_confidence_match_checks_resolved_merges_only() -> None:
    plm = pd.DataFrame({"product_id": ["P1"], "match_score": [0.72]})
    flag = detect_low_confidence_match(plm, "P1", DATE)
    assert flag is not None
    # No row for P2 at all (nothing resolved yet) -> no flag.
    assert detect_low_confidence_match(plm, "P2", DATE) is None


def test_plan_change_respects_known_driver() -> None:
    assert detect_plan_change("bake_plan_change", "P1", DATE, 20.0, 10.0, has_known_driver=True) is None
    flag = detect_plan_change("bake_plan_change", "P1", DATE, 20.0, 10.0, has_known_driver=False)
    assert flag is not None


def test_trailing_avg_receipts_is_delivery_size_not_daily_average() -> None:
    dates = [DATE - pd.Timedelta(days=d) for d in (2, 10, 20)]
    receipts = pd.DataFrame({"product_id": "P1", "date": dates, "qty_received": [30.0, 30.0, 30.0]})
    # Average of actual delivery events (30), not divided across the 28-day window.
    assert trailing_avg_receipts(receipts, "P1", DATE) == 30.0


def test_reason_for_item_priority_and_categories() -> None:
    assert reason_for_item(["phantom_stock"], 5, None, None)[1] == "data_fix"
    assert reason_for_item([], 0, None, None) == ("Enough on the shelf until next delivery", "no_order")
    reason, category = reason_for_item([], 3, 0.2, "On promo")
    assert category == "adjusted" and "On promo" in reason
    assert reason_for_item([], 3, 0.01, None) == ("Demand steady, usual amount", "normal")
