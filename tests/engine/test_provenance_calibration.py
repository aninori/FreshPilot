"""ORD-2/ORD-6: calibrated endpoints and input-specific calculation provenance."""

import pandas as pd
import pytest
from freshpilot.engine.calculations import compute_order
from freshpilot.engine.inventory import estimate_inventory
from freshpilot.engine.newsvendor import target_quantile
from freshpilot.forecasting.horizon import calibrated_quantiles
from freshpilot.forecasting.model import QUANTILE_COLUMNS


def test_ORD2_target_is_the_quantile_without_test_tuned_multiplier():
    assert target_quantile({0.1: 10.0, 0.5: 50.0, 0.9: 90.0}, 0.7) == pytest.approx(70.0)


def test_ORD2_calibrated_endpoints_are_used():
    row = pd.Series({**dict.fromkeys(QUANTILE_COLUMNS, 10.0), "q10_cal": 2.0, "q90_cal": 25.0})
    assert calibrated_quantiles(row)["q10"] == 2.0
    assert calibrated_quantiles(row)["q90"] == 25.0


def test_ORD6_distinct_overrides_have_distinct_reproducible_ids():
    args = ("P1", "order", "2025-06-30", 30.0, 0.0)
    base = compute_order(*args, 0.0, 6, 0, 100, {})
    alternate = compute_order(*args, 12.0, 6, 0, 100, {})
    assert base.calc_id != alternate.calc_id
    assert base.calc_id == compute_order(*args, 0.0, 6, 0, 100, {}).calc_id
    assert alternate.breakdown["on_order_qty"] == 12.0


def test_INV3_zero_book_inventory_is_not_phantom_stock():
    estimate = estimate_inventory(7, 0.0, 20.0, 0.01, 7)
    assert not estimate.is_phantom_stock


def test_ORD6_extra_details_cannot_overwrite_actual_inputs():
    calc = compute_order("P1", "order", "2025-06-30", 30.0, 0.0, 0.0, 6, 0, 100, {"target_qty": 999})
    assert calc.breakdown["target_qty"] == 30.0
