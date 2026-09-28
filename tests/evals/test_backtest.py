"""Backtest correctness on a tiny synthetic dataset — the full 1280-product/28-day
run against BRD's actual G5 acceptance check lives in `make eval`, not here."""

from __future__ import annotations

import pandas as pd
import pytest
from freshpilot.evals.backtest import TEST_DAYS, run_backtest, settle_day
from freshpilot.forecasting.model import QUANTILE_COLUMNS

PRODUCT_ID = "P1"
DEPT_ID = "produce"


@pytest.fixture
def tiny_tables() -> dict[str, pd.DataFrame]:
    n_days = TEST_DAYS + 5
    dates = pd.date_range("2025-01-01", periods=n_days, freq="D")

    products = pd.DataFrame(
        [
            {
                "product_id": PRODUCT_ID,
                "dept_id": DEPT_ID,
                "category": "produce_other",
                "name": "Test item",
                "sell_unit": "ea",
                "case_pack": 6,
                "shelf_life_days": 10,
                "unit_cost": 1.0,
                "unit_price": 2.0,
                "salvage_value": 0.2,
                "is_random_weight": False,
                "min_order_cases": 0,
                "max_order_cases": 0,
            }
        ]
    )

    # Constant demand of 10/day, always fully met historically (true_demand == qty_sold).
    sales_daily = pd.DataFrame(
        {
            "date": dates,
            "store_id": "1",
            "product_id": PRODUCT_ID,
            "qty_sold": 10.0,
            "price": 2.0,
            "on_promo": False,
            "true_demand": 10.0,
        }
    )
    inventory_book = pd.DataFrame(
        {"date": dates, "store_id": "1", "product_id": PRODUCT_ID, "book_on_hand": 50.0, "true_on_hand": 50.0}
    )
    waste_log = pd.DataFrame(
        {"date": dates[:1], "store_id": "1", "product_id": PRODUCT_ID, "qty_wasted": 0.0, "reason": "spoilage"}
    )
    forecasts = pd.DataFrame({"date": dates, "product_id": PRODUCT_ID, **{c: 10.0 for c in QUANTILE_COLUMNS}})
    forecasts["q10_cal"] = 8.0
    forecasts["q90_cal"] = 12.0

    return {
        "products": products,
        "sales_daily": sales_daily,
        "inventory_book": inventory_book,
        "waste_log": waste_log,
        "forecasts": forecasts,
    }


def test_backtest_returns_both_policies(tiny_tables: dict[str, pd.DataFrame]) -> None:
    results = run_backtest(tiny_tables)
    assert set(results.keys()) == {"freshpilot", "baseline"}


def test_backtest_covers_exactly_the_test_window(tiny_tables: dict[str, pd.DataFrame]) -> None:
    results = run_backtest(tiny_tables)
    for m in results.values():
        assert m.total_product_days == TEST_DAYS * 1  # 1 product


def test_backtest_demand_conserved_across_sales_waste_and_lost(tiny_tables: dict[str, pd.DataFrame]) -> None:
    """Every unit of true demand is either sold or lost — nothing vanishes."""
    results = run_backtest(tiny_tables)
    for m in results.values():
        assert abs(m.total_sales + m.total_lost_sales_units - m.total_true_demand) < 1e-6


def test_backtest_no_stockouts_when_stock_is_ample(tiny_tables: dict[str, pd.DataFrame]) -> None:
    """Starting on-hand (50) comfortably covers steady demand (10/day) with any
    reasonable reorder policy — neither policy should stock out here."""
    results = run_backtest(tiny_tables, cadence="daily")
    assert results["freshpilot"].stockout_pct == 0.0


def test_EVAL2_cannot_waste_stock_already_sold():
    assert settle_day(10.0, 0.0, 10.0, 0.5) == (10.0, 0.0, 0.0, 0.0)
    assert settle_day(10.0, 0.0, 8.0, 0.5) == (8.0, 2.0, 0.0, 0.0)


def test_EVAL2_physical_stock_conserved(tiny_tables):
    tiny_tables["waste_log"]["qty_wasted"] = 100.0
    for metrics in run_backtest(tiny_tables).values():
        assert metrics.initial_stock + metrics.total_ordered == pytest.approx(
            metrics.total_sales + metrics.total_waste + metrics.ending_stock
        )


def test_EVAL2_baseline_uses_book_not_true_stock(tiny_tables):
    tiny_tables["inventory_book"]["true_on_hand"] = 0.0
    tiny_tables["inventory_book"]["book_on_hand"] = 100.0
    ledger = []
    run_backtest(tiny_tables, ledger=ledger, cadence="daily")
    first = next(row for row in ledger if row["policy"] == "baseline")
    assert first["ordered"] == 0.0
    assert first["lost"] == 10.0


def test_EVAL2_future_waste_cannot_change_policy(tiny_tables):
    original = run_backtest(tiny_tables)
    future = tiny_tables["waste_log"].copy()
    future["date"] = tiny_tables["sales_daily"]["date"].max()
    future["qty_wasted"] = 10000.0
    tiny_tables["waste_log"] = pd.concat([tiny_tables["waste_log"], future], ignore_index=True)
    assert run_backtest(tiny_tables) == original


def test_FC7_receipts_follow_the_forecast_cycle(tiny_tables):
    ledger = []
    run_backtest(tiny_tables, ledger=ledger)
    assert any(not row["delivery_due"] for row in ledger)
    assert all(row["ordered"] == 0 for row in ledger if not row["delivery_due"])
    assert all(row["cycle_days"] == 2 for row in ledger)


def test_ORD5_bakery_cannot_carry_stock_overnight(tiny_tables):
    tiny_tables["products"]["dept_id"] = "bakery"
    # The catalog can contain longer-lived packaged bread. ORD-5's production
    # plan still explicitly uses a same-day shelf life.
    tiny_tables["products"]["shelf_life_days"] = 5
    ledger = []
    run_backtest(tiny_tables, ledger=ledger)
    assert all(row["ending_stock"] == 0 and row["cycle_days"] == 1 for row in ledger)
