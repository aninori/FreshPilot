"""EVAL-2: 28-day backtest comparing FreshPilot's ordering policy to the baseline
policy (order = last week's same-day sales - on-hand), using TRUE (uncensored)
demand and TRUE shelf stock -- ground truth only the generator can know
(sales_daily.true_demand / inventory_book.true_on_hand, see docs/progress.md M5).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from freshpilot.engine.calculations import round_up_to_case_pack
from freshpilot.engine.inventory import trailing_waste_rate
from freshpilot.engine.newsvendor import compute_critical_ratio, target_quantile
from freshpilot.forecasting.horizon import cycle_days_for, horizon_quantiles
from freshpilot.forecasting.model import QUANTILE_COLUMNS, QUANTILES

TEST_DAYS = 28


def _freshpilot_order(
    product: pd.Series,
    p_forecasts: pd.DataFrame,
    p_waste: pd.DataFrame,
    p_inventory: pd.DataFrame,
    date: pd.Timestamp,
    true_on_hand: float,
) -> float:
    """The order the newsvendor logic wants, given TRUE on-hand directly.

    Deliberately bypasses INV-1's book-vs-shrink estimate: that discount exists
    to correct for *unrecorded* shrink in book_on_hand, which doesn't apply here
    since the backtest already has the true figure — applying it on top would
    double-count spoilage the backtest's own waste step already models.
    """
    fc_row = p_forecasts[p_forecasts["date"] == date]
    if len(fc_row) == 0:
        return 0.0
    daily_quantiles = {col: float(fc_row.iloc[0][col]) for col in QUANTILE_COLUMNS}
    waste_rate = trailing_waste_rate(p_waste, p_inventory, product.product_id, date)
    cr = compute_critical_ratio(
        float(product.unit_cost), float(product.unit_price), float(product.salvage_value),
        int(product.shelf_life_days), waste_rate,
    )
    cycle_days = cycle_days_for(str(product.dept_id))
    horizon = horizon_quantiles(pd.Series(daily_quantiles), cycle_days)
    quantile_values = dict(zip(QUANTILES, (horizon[c] for c in QUANTILE_COLUMNS), strict=True))
    target = target_quantile(quantile_values, cr.cr)
    raw_need = max(0.0, target - true_on_hand)
    order_cases = round_up_to_case_pack(raw_need, int(product.case_pack))
    return float(order_cases * int(product.case_pack))


@dataclass(frozen=True)
class BacktestMetrics:
    policy: str
    total_true_demand: float
    total_sales: float
    total_waste: float
    total_lost_sales_units: float
    stockout_events: int
    total_product_days: int
    waste_pct: float  # waste / (sales + waste): share of handled goods wasted
    stockout_pct: float  # fraction of (product, day) pairs with a stockout
    lost_sales_usd: float
    margin_usd: float


def run_backtest(tables: dict[str, pd.DataFrame]) -> dict[str, BacktestMetrics]:
    products = tables["products"]
    sales_daily = tables["sales_daily"].copy()
    sales_daily["date"] = pd.to_datetime(sales_daily["date"])
    inventory_book = tables["inventory_book"].copy()
    inventory_book["date"] = pd.to_datetime(inventory_book["date"])
    waste_log = tables["waste_log"].copy()
    waste_log["date"] = pd.to_datetime(waste_log["date"])
    forecasts = tables["forecasts"].copy()
    forecasts["date"] = pd.to_datetime(forecasts["date"])

    dates = sorted(sales_daily["date"].unique())
    test_dates = dates[-TEST_DAYS:]
    start_date = test_dates[0]

    products_by_id = products.set_index("product_id")
    true_demand = sales_daily.set_index(["product_id", "date"])["true_demand"]
    actual_sales = sales_daily.set_index(["product_id", "date"])["qty_sold"]
    day0_true_on_hand = inventory_book[inventory_book["date"] == (start_date - pd.Timedelta(days=1))].set_index(
        "product_id"
    )["true_on_hand"]

    results: dict[str, BacktestMetrics] = {}
    for policy in ("freshpilot", "baseline"):
        total_true_demand = total_sales = total_waste = total_lost_sales_units = 0.0
        stockout_events = 0
        lost_sales_usd = margin_usd = 0.0

        for product in products.itertuples(index=False):
            pid = product.product_id
            on_hand = float(day0_true_on_hand.get(pid, 0.0))
            p_inventory = inventory_book[inventory_book["product_id"] == pid]
            p_waste = waste_log[waste_log["product_id"] == pid]
            p_forecasts = forecasts[forecasts["product_id"] == pid]
            # One waste-rate estimate for the whole window: a slow-moving trailing
            # statistic, not worth recomputing 28 times per product per policy.
            wr = trailing_waste_rate(p_waste, p_inventory, pid, start_date)
            cp = int(product.case_pack)

            for date in test_dates:
                demand = float(true_demand.get((pid, date), 0.0))

                if policy == "freshpilot":
                    order = _freshpilot_order(product, p_forecasts, p_waste, p_inventory, date, on_hand)
                else:
                    last_week_sales = float(actual_sales.get((pid, date - pd.Timedelta(days=7)), 0.0))
                    raw_order = max(0.0, last_week_sales - on_hand)
                    order = float(np.ceil(raw_order / cp) * cp) if cp > 0 and raw_order > 0 else 0.0

                available = on_hand + order
                sales = min(demand, available)
                waste = available * wr
                lost = max(0.0, demand - available)
                on_hand = max(0.0, available - sales - waste)

                total_true_demand += demand
                total_sales += sales
                total_waste += waste
                total_lost_sales_units += lost
                if demand > available:
                    stockout_events += 1
                unit_price = float(products_by_id.loc[pid, "unit_price"])
                unit_cost = float(products_by_id.loc[pid, "unit_cost"])
                salvage = float(products_by_id.loc[pid, "salvage_value"])
                lost_sales_usd += lost * unit_price
                margin_usd += sales * (unit_price - unit_cost) - waste * (unit_cost - salvage)

        total_product_days = len(products) * len(test_dates)
        handled = total_sales + total_waste
        results[policy] = BacktestMetrics(
            policy=policy,
            total_true_demand=total_true_demand,
            total_sales=total_sales,
            total_waste=total_waste,
            total_lost_sales_units=total_lost_sales_units,
            stockout_events=stockout_events,
            total_product_days=total_product_days,
            waste_pct=total_waste / handled if handled > 0 else 0.0,
            stockout_pct=stockout_events / total_product_days,
            lost_sales_usd=lost_sales_usd,
            margin_usd=margin_usd,
        )
    return results
