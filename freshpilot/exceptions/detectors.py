"""EXC-1..5: exception detectors run per (product, date) in the pipeline's exception-check stage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from freshpilot.engine.inventory import trailing_waste_rate

SHRINK_SPIKE_MULTIPLE = 2.5
ORDER_SWING_THRESHOLD = 0.60
PLAN_CHANGE_THRESHOLD = 0.15
LOW_CONFIDENCE_MATCH_THRESHOLD = 0.80


@dataclass(frozen=True)
class ExceptionFlag:
    product_id: str
    date: str
    type: str
    details: dict[str, Any]


def detect_shrink_spike(
    waste_log: pd.DataFrame, inventory_book: pd.DataFrame, product_id: str, as_of: pd.Timestamp
) -> ExceptionFlag | None:
    """EXC-2: 7-day waste rate > 2.5x the trailing 28-day rate."""
    rate_7 = trailing_waste_rate(waste_log, inventory_book, product_id, as_of, window_days=7)
    rate_28 = trailing_waste_rate(waste_log, inventory_book, product_id, as_of, window_days=28)
    if rate_28 <= 0:
        return None
    if rate_7 > SHRINK_SPIKE_MULTIPLE * rate_28:
        return ExceptionFlag(
            product_id, str(as_of.date()), "shrink_spike",
            {"rate_7d": rate_7, "rate_28d": rate_28, "multiple": rate_7 / rate_28},
        )
    return None


def trailing_avg_receipts(receipts: pd.DataFrame, product_id: str, as_of: pd.Timestamp, window_days: int = 28) -> float:
    """Average size of an actual delivery (not a per-calendar-day average) — a
    single order/delivery is what's comparable to today's computed order_units,
    since deliveries only land every `cycle_days`, not daily."""
    start = as_of - pd.Timedelta(days=window_days - 1)
    rows = receipts[
        (receipts["product_id"] == product_id) & (receipts["date"] >= start) & (receipts["date"] <= as_of)
    ]
    if len(rows) == 0:
        return 0.0
    return float(rows["qty_received"].mean())


def detect_order_swing(
    trailing_avg_order: float,
    product_id: str,
    as_of: pd.Timestamp,
    order_units: float,
    has_known_driver: bool,
) -> ExceptionFlag | None:
    """EXC-3: order differs from the trailing 4-week average by >60% with no known driver.

    Only meaningful when an order is actually being placed today — most products
    are mid-cycle on any given day (correctly needing 0 more), and "0 vs a typical
    delivery size" isn't a swing, it's just not a reorder day. `trailing_avg_order`
    must come from the same order-computation method as `order_units` (see
    docs/progress.md M5) — comparing against a differently-computed historical
    figure flags a methodology difference, not a real swing.
    """
    if has_known_driver or order_units <= 0 or trailing_avg_order <= 0:
        return None
    avg = trailing_avg_order
    relative_diff = abs(order_units - avg) / avg
    if relative_diff > ORDER_SWING_THRESHOLD:
        return ExceptionFlag(
            product_id, str(as_of.date()), "order_swing",
            {"order_units": order_units, "trailing_avg": avg, "relative_diff": relative_diff},
        )
    return None


def detect_low_confidence_match(
    product_listing_map: pd.DataFrame, product_id: str, as_of: pd.Timestamp
) -> ExceptionFlag | None:
    """EXC-4: "the item's history includes a merge with p < 0.80" — a *resolved*
    merge in product_listing_map, not a pending match_candidates row still waiting
    on CAT-6/CAT-7 review. Every current product_listing_map row is an auto-merge
    at >=0.90 (CAT-5), so this only fires once a human/agent has approved a
    lower-confidence merge — which is exactly when it should.
    """
    rows = product_listing_map[product_listing_map["product_id"] == product_id]
    if len(rows) == 0:
        return None
    worst_score = float(rows["match_score"].min())
    if worst_score < LOW_CONFIDENCE_MATCH_THRESHOLD:
        return ExceptionFlag(
            product_id, str(as_of.date()), "low_confidence_match", {"match_score": worst_score}
        )
    return None


def detect_plan_change(
    plan_type: str,
    product_id: str,
    as_of: pd.Timestamp,
    planned_qty: float,
    trailing_avg_qty: float,
    has_known_driver: bool = False,
) -> ExceptionFlag | None:
    """EXC-5: yield_plan_change / bake_plan_change when the plan deviates from usual by >15%.

    `has_known_driver` isn't in BRD's literal EXC-5 wording (unlike EXC-3's order_swing,
    which explicitly excludes explained swings), but the same system philosophy applies:
    a promo/holiday/weather-explained change belongs in the "adjusted" item_reasons
    category, not a manager escalation — see docs/progress.md M5.
    """
    if trailing_avg_qty <= 0 or has_known_driver:
        return None
    relative_diff = abs(planned_qty - trailing_avg_qty) / trailing_avg_qty
    if relative_diff > PLAN_CHANGE_THRESHOLD:
        return ExceptionFlag(
            product_id, str(as_of.date()), plan_type,
            {"planned_qty": planned_qty, "trailing_avg": trailing_avg_qty, "relative_diff": relative_diff},
        )
    return None
