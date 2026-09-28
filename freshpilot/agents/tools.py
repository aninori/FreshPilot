"""AGT-1: read-only tools the investigator may call. Every number an agent
narrates must trace back to one of these (§5 principle 3: agents may only
read or simulate; simulate_order is the one tool that computes a number,
and it never writes an order).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import lightgbm as lgb
import pandas as pd

from freshpilot.agents.budget import RunBudget
from freshpilot.engine.calculations import OrderCalculation
from freshpilot.engine.simulate import EngineContext
from freshpilot.engine.simulate import simulate_order as engine_simulate_order
from freshpilot.forecasting.explain import top_drivers
from freshpilot.forecasting.features import build_feature_frame
from freshpilot.forecasting.model import QUANTILE_COLUMNS


@dataclass
class ToolContext:
    products: pd.DataFrame
    sales_daily: pd.DataFrame
    inventory_book: pd.DataFrame
    waste_log: pd.DataFrame
    forecasts: pd.DataFrame
    promotions: pd.DataFrame
    weather: pd.DataFrame
    calendar: pd.DataFrame
    product_listing_map: pd.DataFrame
    match_candidates: pd.DataFrame
    demo_date: pd.Timestamp
    p50_model: lgb.Booster | None = None  # for explain_forecast; None if models weren't loaded
    tool_outputs: list[dict[str, Any]] = field(default_factory=list)
    calculations: dict[str, dict[str, Any]] = field(default_factory=dict)
    budget: RunBudget | None = None
    deadline: float = 0.0
    llm_tool_calls: int = 0

    def engine_context(self) -> EngineContext:
        return EngineContext(
            self.products, self.forecasts, self.inventory_book, self.waste_log, self.sales_daily, self.demo_date
        )


def get_sales_history(ctx: ToolContext, product_id: str, days: int = 28) -> dict[str, Any]:
    rows = ctx.sales_daily[
        (ctx.sales_daily["product_id"] == product_id) & (ctx.sales_daily["date"] <= ctx.demo_date)
    ].sort_values("date").tail(min(max(days, 1), 180))
    return {
        "product_id": product_id,
        "history": [
            {"date": str(r.date.date()), "qty_sold": float(r.qty_sold), "on_promo": bool(r.on_promo)}
            for r in rows.itertuples(index=False)
        ],
    }


def get_inventory(ctx: ToolContext, product_id: str) -> dict[str, Any]:
    rows = ctx.inventory_book[
        (ctx.inventory_book["product_id"] == product_id) & (ctx.inventory_book["date"] <= ctx.demo_date)
    ].sort_values("date")
    if len(rows) == 0:
        return {"product_id": product_id, "book_on_hand": None}
    last = rows.iloc[-1]
    return {"product_id": product_id, "date": str(last.date.date()), "book_on_hand": float(last.book_on_hand)}


def get_forecast(ctx: ToolContext, product_id: str) -> dict[str, Any]:
    rows = ctx.forecasts[
        (ctx.forecasts["product_id"] == product_id) & (ctx.forecasts["date"] == ctx.demo_date)
    ]
    if len(rows) == 0:
        return {"product_id": product_id, "quantiles": None}
    last = rows.iloc[-1]
    return {
        "product_id": product_id,
        "date": str(last.date.date()),
        "quantiles": {col: float(last[col]) for col in QUANTILE_COLUMNS},
        "q10_cal": float(last["q10_cal"]),
        "q90_cal": float(last["q90_cal"]),
    }


def explain_forecast(ctx: ToolContext, product_id: str) -> dict[str, Any]:
    if ctx.p50_model is None:
        return {"product_id": product_id, "drivers": []}
    features = build_feature_frame(ctx.sales_daily, ctx.calendar, ctx.weather, ctx.promotions, ctx.products)
    row = features[(features["product_id"] == product_id) & (features["date"] == ctx.demo_date)]
    if len(row) == 0:
        return {"product_id": product_id, "drivers": []}
    drivers = top_drivers(ctx.p50_model, row.iloc[[0]], n=3)
    return {"product_id": product_id, "drivers": drivers}


def get_promotions(ctx: ToolContext, product_id: str) -> dict[str, Any]:
    rows = ctx.promotions[ctx.promotions["product_id"] == product_id]
    return {
        "product_id": product_id,
        "promotions": [
            {
                "start_date": str(pd.Timestamp(r.start_date).date()),
                "end_date": str(pd.Timestamp(r.end_date).date()),
                "discount_pct": float(r.discount_pct),
                "expected_lift": float(r.expected_lift),
            }
            for r in rows.itertuples(index=False)
        ],
    }


def get_weather(ctx: ToolContext, dates: list[str]) -> dict[str, Any]:
    wanted = {pd.Timestamp(d) for d in dates}
    rows = ctx.weather[ctx.weather["date"].isin(wanted)]
    return {
        "weather": [
            {
                "date": str(r.date.date()),
                "temp_f": float(r.temp_f),
                "precip_prob": float(r.precip_prob),
                "condition": r.condition,
            }
            for r in rows.itertuples(index=False)
        ]
    }


def get_waste_log(ctx: ToolContext, product_id: str, days: int = 28) -> dict[str, Any]:
    rows = ctx.waste_log[
        (ctx.waste_log["product_id"] == product_id) & (ctx.waste_log["date"] <= ctx.demo_date)
    ].sort_values("date").tail(min(max(days, 1), 180))
    return {
        "product_id": product_id,
        "entries": [
            {"date": str(r.date.date()), "qty_wasted": float(r.qty_wasted), "reason": r.reason}
            for r in rows.itertuples(index=False)
        ],
    }


def get_catalog_match(ctx: ToolContext, product_id: str) -> dict[str, Any]:
    resolved = ctx.product_listing_map[ctx.product_listing_map["product_id"] == product_id]
    candidate = ctx.match_candidates[ctx.match_candidates["product_id"] == product_id]
    return {
        "product_id": product_id,
        "resolved_matches": [
            {"listing_id": r.listing_id, "match_score": float(r.match_score), "method": r.method}
            for r in resolved.itertuples(index=False)
        ],
        "current_candidate": (
            {
                "listing_id": candidate.iloc[0]["listing_id"],
                "score": float(candidate.iloc[0]["score"]),
                "decision": candidate.iloc[0]["decision"],
            }
            if len(candidate)
            else None
        ),
    }


def simulate_order(ctx: ToolContext, product_id: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """ORD-7: the only tool that computes a number, and it never writes an order."""
    calc: OrderCalculation = engine_simulate_order(ctx.engine_context(), product_id, overrides)
    result = {
        "calc_id": calc.calc_id,
        "calc_type": calc.calc_type,
        "date": calc.date,
        "product_id": calc.product_id,
        "target_qty": calc.target_qty,
        "estimated_on_hand": calc.estimated_on_hand,
        "order_cases": calc.order_cases,
        "order_units": calc.order_units,
        "quantity_unit": "units" if calc.calc_type == "bake" else "cases",
        "breakdown": calc.breakdown,
    }
    ctx.calculations[calc.calc_id] = result
    return result


def call_tool(ctx: ToolContext, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Record trusted results before exposing them to the model (AGT-3/GRD-3)."""
    started = time.monotonic()
    result = TOOLS[name](ctx, **arguments)
    source_ref = str(result.get("calc_id", f"tool-{len(ctx.tool_outputs) + 1}"))
    ctx.tool_outputs.append({"name": name, "arguments": arguments, "source_ref": source_ref, "result": result,
                             "duration_s": time.monotonic() - started})
    return {**result, "source_ref": source_ref}


TOOLS: dict[str, Callable[..., dict[str, Any]]] = {
    "get_sales_history": get_sales_history,
    "get_inventory": get_inventory,
    "get_forecast": get_forecast,
    "explain_forecast": explain_forecast,
    "get_promotions": get_promotions,
    "get_weather": get_weather,
    "get_waste_log": get_waste_log,
    "get_catalog_match": get_catalog_match,
    "simulate_order": simulate_order,
}
