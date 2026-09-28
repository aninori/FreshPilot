"""ORD-7: simulate_order(product_id, overrides) — the only engine entry point
agents may call (§5 principle 3: agents may only read or simulate). Never writes
an order; the pipeline/API layer decides when a calculation becomes a real order.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import pandas as pd

from freshpilot.engine.calculations import OrderCalculation, compute_order
from freshpilot.engine.inventory import consecutive_zero_sales_days, estimate_inventory, trailing_waste_rate
from freshpilot.engine.newsvendor import compute_critical_ratio, target_quantile
from freshpilot.forecasting.horizon import calibrated_quantiles, cycle_days_for, horizon_quantiles
from freshpilot.forecasting.model import QUANTILE_COLUMNS, QUANTILES


@dataclass(frozen=True)
class EngineContext:
    products: pd.DataFrame
    forecasts: pd.DataFrame
    inventory_book: pd.DataFrame
    waste_log: pd.DataFrame
    sales_daily: pd.DataFrame
    demo_date: pd.Timestamp


def _latest_book_on_hand(ctx: EngineContext, product_id: str) -> float:
    rows = ctx.inventory_book[
        (ctx.inventory_book["product_id"] == product_id) & (ctx.inventory_book["date"] <= ctx.demo_date)
    ]
    if len(rows) == 0:
        return 0.0
    return float(rows.sort_values("date").iloc[-1]["book_on_hand"])


def _forecast_quantiles(ctx: EngineContext, product_id: str) -> dict[str, float]:
    rows = ctx.forecasts[(ctx.forecasts["product_id"] == product_id) & (ctx.forecasts["date"] == ctx.demo_date)]
    if len(rows) == 0:
        raise ValueError(f"No forecast for {product_id} on {ctx.demo_date.date()}")
    row = rows.iloc[0]
    return calibrated_quantiles(row)


def simulate_order(ctx: EngineContext, product_id: str, overrides: dict[str, Any] | None = None) -> OrderCalculation:
    overrides = overrides or {}
    for name, value in overrides.items():
        if name not in {"book_on_hand", "on_order_qty"}:
            raise ValueError(f"Unsupported simulation override: {name}")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"Simulation override {name} must be finite and nonnegative")
    product = ctx.products[ctx.products["product_id"] == product_id].iloc[0]
    is_bakery = str(product["dept_id"]) == "bakery"
    shelf_life_days = 1 if is_bakery else int(product["shelf_life_days"])

    book_on_hand = float(overrides.get("book_on_hand", _latest_book_on_hand(ctx, product_id)))
    waste_rate = trailing_waste_rate(ctx.waste_log, ctx.inventory_book, product_id, ctx.demo_date)
    zeros = consecutive_zero_sales_days(ctx.sales_daily, product_id, ctx.demo_date)

    daily_quantiles = _forecast_quantiles(ctx, product_id)
    daily_mean_demand = daily_quantiles["q50"]

    inv_estimate = estimate_inventory(
        shelf_life_days=shelf_life_days,
        book_on_hand=book_on_hand,
        daily_mean_demand=daily_mean_demand,
        waste_rate_per_day=waste_rate,
        consecutive_zeros=zeros,
    )

    cr = compute_critical_ratio(
        unit_cost=float(product["unit_cost"]),
        unit_price=float(product["unit_price"]),
        salvage_value=float(product["salvage_value"]),
        shelf_life_days=shelf_life_days,
        waste_rate_per_day=waste_rate,
    )

    cycle_days = cycle_days_for(str(product["dept_id"]))
    horizon = horizon_quantiles(pd.Series(daily_quantiles), cycle_days)
    quantile_values = dict(zip(QUANTILES, (horizon[col] for col in QUANTILE_COLUMNS), strict=True))
    target = target_quantile(quantile_values, cr.cr)
    case_pack = 1 if is_bakery else int(product["case_pack"])
    max_cases = int(product["max_order_cases"])
    capacity = product.get("max_shelf_capacity")
    if capacity is not None:
        capacity_cases = math.floor(float(capacity) / case_pack)
        # compute_order uses zero as an unlimited maximum, so cap the target too.
        target = min(target, inv_estimate.estimated_on_hand + max(0, capacity_cases) * case_pack)
        max_cases = min(max_cases, capacity_cases) if max_cases > 0 else capacity_cases

    return compute_order(
        product_id=product_id,
        calc_type="bake" if is_bakery else "order",
        date=str(ctx.demo_date.date()) if hasattr(ctx.demo_date, "date") else str(ctx.demo_date),
        target_qty=target,
        estimated_on_hand=inv_estimate.estimated_on_hand,
        on_order_qty=float(overrides.get("on_order_qty", 0.0)),
        case_pack=case_pack,
        min_order_cases=int(product["min_order_cases"]),
        max_order_cases=max_cases,
        breakdown_extra={
            "cr": cr.cr,
            "cu": cr.cu,
            "co": cr.co,
            "book_on_hand": book_on_hand,
            "is_phantom_stock": inv_estimate.is_phantom_stock,
            "zero_run_probability": inv_estimate.zero_run_probability,
            "consecutive_zero_sales_days": zeros,
            "cycle_days": cycle_days,
        },
    )
