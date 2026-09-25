"""ORD-5: bakery bake quantity via the same newsvendor logic, same-day shelf life,
no case-pack rounding (a bake plan is a quantity, not a case order).
"""

from __future__ import annotations

import math

from freshpilot.engine.newsvendor import CriticalRatio, compute_critical_ratio, target_quantile

SAME_DAY_SHELF_LIFE = 1


def compute_bake_quantity(
    unit_cost: float,
    unit_price: float,
    salvage_value: float,
    waste_rate_per_day: float,
    quantiles: dict[float, float],
    estimated_on_hand: float,
) -> tuple[float, CriticalRatio]:
    cr = compute_critical_ratio(
        unit_cost=unit_cost,
        unit_price=unit_price,
        salvage_value=salvage_value,
        shelf_life_days=SAME_DAY_SHELF_LIFE,
        waste_rate_per_day=waste_rate_per_day,
    )
    target = target_quantile(quantiles, cr.cr)
    bake_qty = max(0.0, math.ceil(target - estimated_on_hand))
    return bake_qty, cr
