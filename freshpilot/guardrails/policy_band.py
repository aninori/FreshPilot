"""GRD-2: recommended quantity must be within [0.5x, 1.6x] of the engine's base
order, and not exceed max shelf capacity.
"""

from __future__ import annotations

from dataclasses import dataclass

POLICY_BAND_LOW = 0.5
POLICY_BAND_HIGH = 1.6


@dataclass(frozen=True)
class PolicyBandResult:
    within_band: bool
    low: float
    high: float


def check_policy_band(
    recommended_qty: float, base_order_qty: float, max_shelf_capacity: float | None = None
) -> PolicyBandResult:
    low = base_order_qty * POLICY_BAND_LOW
    high = base_order_qty * POLICY_BAND_HIGH
    within = low <= recommended_qty <= high
    if max_shelf_capacity is not None and recommended_qty > max_shelf_capacity:
        within = False
    return PolicyBandResult(within_band=within, low=low, high=high)
