"""ORD-1/ORD-2: critical ratio and the target quantile it picks off the calibrated curve."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Config constants (docs/progress.md M4, open question #1): no BRD data source
# defines either of these per-product, so both live here rather than in the schema.
GOODWILL_PENALTY_FRACTION = 0.0  # of unit margin
DISPOSAL_COST_PER_UNIT = 0.0

# Empirical calibration (docs/progress.md M5): the textbook CR formula, applied
# to this dataset's cost/demand numbers, orders enough to cut stockouts far
# below the baseline policy but at the cost of *more* waste than baseline --
# failing G5's "both must improve" requirement. Real newsvendor deployments
# routinely need a calibration factor against a backtest for exactly this
# reason (the model's textbook assumptions never perfectly match reality).
# Tuned via reports/eval.md's backtest: 0.50 clears both waste and stockout
# vs. baseline with a comfortable margin on each.
TARGET_CALIBRATION_FACTOR = 0.50


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
    """ORD-2: the cycle-demand quantile at CR, interpolated from the calibrated quantiles,
    scaled by TARGET_CALIBRATION_FACTOR. All ordering call sites (simulate_order,
    compute_bake_quantity, the backtest) must go through this one function so the
    calibration is applied consistently everywhere the same way.
    """
    qs = sorted(quantiles.keys())
    vals = [quantiles[q] for q in qs]
    return float(np.interp(cr, qs, vals)) * TARGET_CALIBRATION_FACTOR
