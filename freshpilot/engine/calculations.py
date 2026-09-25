"""ORD-3/ORD-6: order quantity from target vs. on-hand, case-pack rounded, with a
deterministic calc_id and a full breakdown for the `calculations` table.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class OrderCalculation:
    calc_id: str
    product_id: str
    calc_type: str
    date: str
    target_qty: float
    estimated_on_hand: float
    on_order_qty: float
    case_pack: int
    order_cases: int
    order_units: float
    breakdown: dict[str, Any] = field(default_factory=dict)


def round_up_to_case_pack(units: float, case_pack: int) -> int:
    if case_pack <= 0 or units <= 0:
        return 0
    return math.ceil(units / case_pack)


def compute_order(
    product_id: str,
    calc_type: str,
    date: str,
    target_qty: float,
    estimated_on_hand: float,
    on_order_qty: float,
    case_pack: int,
    min_order_cases: int,
    max_order_cases: int,
    breakdown_extra: dict[str, Any],
) -> OrderCalculation:
    """ORD-3: order = max(0, target - on_hand - on_order), case-pack rounded, clipped
    to [min_order_cases, max_order_cases]. ORD-6: every call carries a calc_id and a
    full breakdown of the inputs that produced it.
    """
    raw_need = max(0.0, target_qty - estimated_on_hand - on_order_qty)
    order_cases = round_up_to_case_pack(raw_need, case_pack)
    order_cases = max(order_cases, min_order_cases)
    if max_order_cases > 0:
        order_cases = min(order_cases, max_order_cases)
    order_units = float(order_cases * case_pack)

    # Deterministic, not random (NFR-2 / CLAUDE.md rule 6): identical inputs must
    # always produce the same calc_id.
    calc_id = f"CALC-{calc_type}-{product_id}-{date}"

    breakdown = {
        "target_qty": target_qty,
        "estimated_on_hand": estimated_on_hand,
        "on_order_qty": on_order_qty,
        "raw_need": raw_need,
        "case_pack": case_pack,
        "min_order_cases": min_order_cases,
        "max_order_cases": max_order_cases,
        **breakdown_extra,
    }
    return OrderCalculation(
        calc_id=calc_id,
        product_id=product_id,
        calc_type=calc_type,
        date=date,
        target_qty=target_qty,
        estimated_on_hand=estimated_on_hand,
        on_order_qty=on_order_qty,
        case_pack=case_pack,
        order_cases=order_cases,
        order_units=order_units,
        breakdown=breakdown,
    )
