"""ORD-1/ORD-2: critical ratio and the target quantile it picks off the calibrated curve."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Config constants (docs/progress.md M4, open question #1): no BRD data source
# defines either of these per-product, so both live here rather than in the schema.
GOODWILL_PENALTY_FRACTION = 0.0  # of unit margin
DISPOSAL_COST_PER_UNIT = 0.0

@dataclass(frozen=True)
class CriticalRatio:
    cu: float
    co: float
    cr: float


def compute_critical_ratio(
    unit_cost: float,
    unit_price: float,
    salvage_value: float,
    shelf_life_days: int,
    waste_rate_per_day: float,
) -> CriticalRatio:
    """ORD-1: CR = Cu / (Cu + Co), Co adjusted for expected spoilage over shelf life."""
    unit_margin = unit_price - unit_cost
    cu = unit_margin + unit_margin * GOODWILL_PENALTY_FRACTION

    expected_spoilage_fraction = min(waste_rate_per_day * shelf_life_days, 1.0)
    co_base = unit_cost - salvage_value + DISPOSAL_COST_PER_UNIT
    co = co_base * (1 + expected_spoilage_fraction)

    total = cu + co
    cr = cu / total if total > 0 else 0.5
    return CriticalRatio(cu=cu, co=co, cr=cr)


def target_quantile(quantiles: dict[float, float], cr: float) -> float:
    """ORD-2: interpolate the calibrated cycle-demand curve at the critical ratio."""
    qs = sorted(quantiles.keys())
    vals = [quantiles[q] for q in qs]
    return float(np.interp(cr, qs, vals))
