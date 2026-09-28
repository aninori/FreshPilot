"""Runs EXC-1..6 for the demo date (the last day forecasts cover) across every
product, writing `exceptions` and `item_reasons`, and reports the escalation rate
(share of items with >=1 exception) against G6's <=1% target.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from freshpilot.config import SEED, load_settings
from freshpilot.engine.bakery import SAME_DAY_SHELF_LIFE, compute_bake_quantity
from freshpilot.engine.inventory import trailing_waste_rate
from freshpilot.engine.newsvendor import compute_critical_ratio, target_quantile
from freshpilot.engine.simulate import EngineContext, simulate_order
from freshpilot.exceptions.detectors import (
    ExceptionFlag,
    detect_low_confidence_match,
    detect_order_swing,
    detect_plan_change,
    detect_shrink_spike,
)
from freshpilot.exceptions.reasons import reason_for_item
from freshpilot.forecasting.horizon import calibrated_quantiles
from freshpilot.forecasting.model import QUANTILES

DB_PATH = Path("data") / "freshpilot.duckdb"
ESCALATION_TARGET = 0.01


def _driver_label(row: pd.Series) -> str | None:
    """A cheap, non-SHAP driver label for EXC-6's reason text — proper SHAP-backed
    narration is an M6 agent concern; this stage just needs a plausible one-liner."""
    if bool(row.get("on_promo", False)):
        return "On promo"
    if bool(row.get("is_holiday", False)):
        return "Holiday"
    if float(row.get("precip_prob", 0.0)) > 0.5:
        return "Rain"
    if float(row.get("temp_f", 0.0)) > 80:
        return "Warm weather"
    return None


def run(db_path: Path = DB_PATH, seed: int = SEED, date: str | None = None) -> dict[str, Any]:
    con = duckdb.connect(str(db_path))
    try:
        products = con.execute("SELECT * FROM products").df()
        forecasts = con.execute("SELECT * FROM forecasts").df()
        forecasts["date"] = pd.to_datetime(forecasts["date"])
        inventory_book = con.execute("SELECT * FROM inventory_book").df()
        inventory_book["date"] = pd.to_datetime(inventory_book["date"])
        waste_log = con.execute("SELECT * FROM waste_log").df()
        waste_log["date"] = pd.to_datetime(waste_log["date"])
        sales_daily = con.execute("SELECT * FROM sales_daily").df()
        sales_daily["date"] = pd.to_datetime(sales_daily["date"])
        product_listing_map = con.execute("SELECT * FROM product_listing_map").df()
        calendar = con.execute("SELECT * FROM calendar").df()
        calendar["date"] = pd.to_datetime(calendar["date"])
        weather = con.execute("SELECT * FROM weather").df()
        weather["date"] = pd.to_datetime(weather["date"])
        promotions = con.execute("SELECT * FROM promotions").df()
        promotions["start_date"] = pd.to_datetime(promotions["start_date"])
        promotions["end_date"] = pd.to_datetime(promotions["end_date"])

        requested = date or load_settings().demo_date
        demo_date = pd.Timestamp(requested) if requested else forecasts["date"].max()
        if demo_date not in set(forecasts["date"]):
            raise ValueError(f"No forecasts available for demo date {demo_date}")

        day_calendar = calendar[calendar["date"] == demo_date].iloc[0]
        day_weather = weather[weather["date"] == demo_date].iloc[0]
        promo_today = set(
            promotions[(promotions["start_date"] <= demo_date) & (promotions["end_date"] >= demo_date)]["product_id"]
        )

        exceptions: list[ExceptionFlag] = []
        item_rows: list[dict[str, object]] = []
        # EXC-3 says "trailing 4-week average" -- 28 days, not 7; a wider window
        # also means more nonzero-order samples per product, so the average is
        # less at the mercy of which few days landed in a shorter lookback.
        lookback_dates = [demo_date - pd.Timedelta(days=d) for d in range(1, 29)]

        for product in products.itertuples(index=False):
            pid = product.product_id
            # Pre-slice once per product: every helper below filters by product_id
            # internally, so handing it an already-small frame instead of the
            # full 1280-product one avoids rescanning it on every lookback call.
            p_inventory = inventory_book[inventory_book["product_id"] == pid]
            p_waste = waste_log[waste_log["product_id"] == pid]
            p_sales = sales_daily[sales_daily["product_id"] == pid]
            p_forecasts = forecasts[forecasts["product_id"] == pid]

            calc = simulate_order(EngineContext(products, p_forecasts, p_inventory, p_waste, p_sales, demo_date), pid)

            flags: list[str] = []
            if calc.breakdown["is_phantom_stock"]:
                flags.append("phantom_stock")
                exceptions.append(
                    ExceptionFlag(
                        pid, str(demo_date.date()), "phantom_stock",
                        {"zero_run_probability": calc.breakdown["zero_run_probability"]},
                    )
                )

            flag = detect_shrink_spike(p_waste, p_inventory, pid, demo_date)
            if flag:
                exceptions.append(flag)
                flags.append(flag.type)

            # Compare the pre-rounding target, not order_units: case-pack rounding
            # (e.g. 8/16/24) makes the actual order lumpy — a small, ordinary
            # change in underlying demand can look like a 50-100% "swing" in the
            # rounded quantity even though nothing unusual happened. The target
            # is a smooth function of forecasted demand on both sides of the
            # comparison, so it isolates genuine demand anomalies from rounding
            # noise. Cheap too: a forecasts-table lookup, no re-simulation.
            cycle_days = calc.breakdown["cycle_days"]
            prior_targets = []
            for d in lookback_dates:
                fc_d = p_forecasts[p_forecasts["date"] == d]
                if len(fc_d):
                    # Historical median cycle demand is a proxy for usual demand.
                    prior_targets.append(float(fc_d.iloc[0]["q50"]) * cycle_days)
            trailing_avg_target = sum(prior_targets) / len(prior_targets) if len(prior_targets) >= 2 else 0.0
            has_driver = (
                pid in promo_today
                or bool(day_calendar["is_holiday"])
                or float(day_weather["precip_prob"]) > 0.5
                or float(day_weather["temp_f"]) > 80
            )
            flag = detect_order_swing(trailing_avg_target, pid, demo_date, calc.target_qty, has_driver)
            if flag:
                exceptions.append(flag)
                flags.append(flag.type)

            flag = detect_low_confidence_match(product_listing_map, pid, demo_date)
            if flag:
                exceptions.append(flag)
                flags.append(flag.type)

            if product.dept_id == "bakery":
                fc_row = p_forecasts[p_forecasts["date"] == demo_date]
                quantiles = (
                    dict(zip(QUANTILES, calibrated_quantiles(fc_row.iloc[0]).values(), strict=True))
                    if len(fc_row) else dict.fromkeys(QUANTILES, 0.0)
                )
                waste_rate = trailing_waste_rate(p_waste, p_inventory, pid, demo_date)
                bake_qty, _ = compute_bake_quantity(
                    float(product.unit_cost), float(product.unit_price), float(product.salvage_value),
                    waste_rate, quantiles, calc.estimated_on_hand,
                )
                # Compare the pre-on-hand-subtraction bake target, not the final
                # bake_qty: same lumpiness issue as order_swing above, but driven
                # by day-to-day on-hand noise instead of case-pack rounding. The
                # target is smooth; bake_qty = target - on_hand isn't.
                bake_target = target_quantile(quantiles, compute_critical_ratio(
                    float(product.unit_cost), float(product.unit_price), float(product.salvage_value),
                    SAME_DAY_SHELF_LIFE, waste_rate,
                ).cr)
                prior_targets_bake = []
                # Same weekday, 4 prior weeks -- not the trailing 7 calendar days,
                # which blends every day-of-week together. A Saturday is ~20-30%
                # above the weekly mean by design (DOW_EFFECT), so comparing it to
                # a blended average flags ordinary weekly seasonality as a "swing."
                for d in [demo_date - pd.Timedelta(days=7 * k) for k in range(1, 9)]:
                    fc_d = p_forecasts[p_forecasts["date"] == d]
                    if len(fc_d) == 0:
                        continue
                    q_d = dict(zip(QUANTILES, calibrated_quantiles(fc_d.iloc[0]).values(), strict=True))
                    cr_d = compute_critical_ratio(
                        float(product.unit_cost), float(product.unit_price), float(product.salvage_value),
                        SAME_DAY_SHELF_LIFE, trailing_waste_rate(p_waste, p_inventory, pid, d),
                    )
                    prior_targets_bake.append(target_quantile(q_d, cr_d.cr))
                trailing_avg_bake = (
                    sum(prior_targets_bake) / len(prior_targets_bake) if len(prior_targets_bake) >= 2 else 0.0
                )
                flag = detect_plan_change(
                    "bake_plan_change", pid, demo_date, bake_target, trailing_avg_bake, has_known_driver=has_driver
                )
                if flag:
                    exceptions.append(flag)
                    flags.append(flag.type)

            pct_change = (
                (calc.target_qty - trailing_avg_target) / trailing_avg_target if trailing_avg_target > 0 else None
            )
            driver_row = pd.Series(
                {
                    "on_promo": pid in promo_today,
                    "is_holiday": bool(day_calendar["is_holiday"]),
                    "precip_prob": float(day_weather["precip_prob"]),
                    "temp_f": float(day_weather["temp_f"]),
                }
            )
            reason, category = reason_for_item(flags, calc.order_cases, pct_change, _driver_label(driver_row))
            item_rows.append(
                {"product_id": pid, "date": str(demo_date.date()), "reason": reason, "category": category}
            )

        exceptions_df = pd.DataFrame(
            [
                {"product_id": e.product_id, "date": e.date, "type": e.type, "details": json.dumps(e.details)}
                for e in exceptions
            ]
        )
        item_reasons_df = pd.DataFrame(item_rows)

        con.execute("DELETE FROM exceptions")
        if len(exceptions_df):
            con.append("exceptions", exceptions_df)
        con.execute("DELETE FROM item_reasons")
        con.append("item_reasons", item_reasons_df)

        escalated_products = set(exceptions_df["product_id"]) if len(exceptions_df) else set()
        escalation_rate = len(escalated_products) / len(products)
        exceptions_by_type = exceptions_df["type"].value_counts().to_dict() if len(exceptions_df) else {}

        return {
            "escalation_rate": escalation_rate,
            "n_escalated": len(escalated_products),
            "n_products": len(products),
            "exceptions_by_type": exceptions_by_type,
            "item_reasons_by_category": item_reasons_df["category"].value_counts().to_dict(),
        }
    finally:
        con.close()


def main() -> None:
    result = run()
    print("Exceptions (EXC-1..6)")
    print(
        f"  Escalated: {result['n_escalated']}/{result['n_products']} "
        f"({result['escalation_rate']:.2%}, target <= {ESCALATION_TARGET:.0%})"
    )
    print(f"  By type: {result['exceptions_by_type']}")
    print(f"  Item reasons by category: {result['item_reasons_by_category']}")


if __name__ == "__main__":
    main()
