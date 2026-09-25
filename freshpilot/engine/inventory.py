"""INV-1..3: estimate on-hand accounting for unrecorded shrink, and phantom-stock detection."""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

PHANTOM_PROBABILITY_THRESHOLD = 0.001


@dataclass(frozen=True)
class InventoryEstimate:
    book_on_hand: float
    estimated_on_hand: float
    is_phantom_stock: bool
    consecutive_zero_sales_days: int
    zero_run_probability: float


def trailing_waste_rate(
    waste_log: pd.DataFrame,
    inventory_book: pd.DataFrame,
    product_id: str,
    as_of: pd.Timestamp,
    window_days: int = 28,
) -> float:
    """Average fraction of on-hand stock wasted per day, over the trailing window."""
    start = as_of - pd.Timedelta(days=window_days - 1)
    waste = waste_log[
        (waste_log["product_id"] == product_id) & (waste_log["date"] >= start) & (waste_log["date"] <= as_of)
    ]
    inv = inventory_book[
        (inventory_book["product_id"] == product_id)
        & (inventory_book["date"] >= start)
        & (inventory_book["date"] <= as_of)
    ]
    if len(inv) == 0:
        return 0.0
    avg_on_hand = float(inv["book_on_hand"].mean())
    if avg_on_hand <= 0:
        return 0.0
    total_waste = float(waste["qty_wasted"].sum())
    return total_waste / (avg_on_hand * window_days)


def consecutive_zero_sales_days(sales_daily: pd.DataFrame, product_id: str, as_of: pd.Timestamp) -> int:
    """How many days, counting back from `as_of`, this product sold zero units."""
    history = sales_daily[(sales_daily["product_id"] == product_id) & (sales_daily["date"] <= as_of)].sort_values(
        "date"
    )
    count = 0
    for qty in reversed(history["qty_sold"].to_numpy()):
        if qty == 0:
            count += 1
        else:
            break
    return count


def zero_run_probability(daily_mean_demand: float, k: int) -> float:
    """INV-2: P(zero sales for k consecutive days | stock truly on shelf), from the
    Poisson distribution with the forecast daily mean."""
    if k <= 0:
        return 1.0
    return math.exp(-daily_mean_demand * k)


def estimate_inventory(
    shelf_life_days: int,
    book_on_hand: float,
    daily_mean_demand: float,
    waste_rate_per_day: float,
    consecutive_zeros: int,
) -> InventoryEstimate:
    """INV-1 (shrink-adjusted estimate) + INV-2/INV-3 (phantom-stock override)."""
    assumed_lot_age_days = shelf_life_days / 2  # docs/progress.md M4: no FIFO lot tracking
    expected_shrink_fraction = min(waste_rate_per_day * assumed_lot_age_days, 1.0)
    shrink_adjusted = book_on_hand * (1 - expected_shrink_fraction)

    p_zero = zero_run_probability(daily_mean_demand, consecutive_zeros)
    is_phantom = p_zero < PHANTOM_PROBABILITY_THRESHOLD

    if is_phantom:
        mu_effective = -math.log(PHANTOM_PROBABILITY_THRESHOLD) / consecutive_zeros
        estimated = min(book_on_hand, mu_effective, shrink_adjusted)
    else:
        estimated = shrink_adjusted

    return InventoryEstimate(
        book_on_hand=book_on_hand,
        estimated_on_hand=max(estimated, 0.0),
        is_phantom_stock=is_phantom,
        consecutive_zero_sales_days=consecutive_zeros,
        zero_run_probability=p_zero,
    )
