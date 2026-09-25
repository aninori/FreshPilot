"""ORD-4: yield-aware ordering — the parent order covers the max child requirement,
and surplus children get a disposition.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class YieldChildRequirement:
    child_product_id: str
    child_target_qty: float
    yield_per_parent: float
    implied_parent_qty: float


@dataclass(frozen=True)
class YieldPlan:
    parent_product_id: str
    parent_order_qty: float
    children: list[YieldChildRequirement]
    surplus: dict[str, float]  # child_product_id -> qty produced beyond that child's own target


def compute_yield_plan(
    parent_product_id: str, child_requirements: list[tuple[str, float, float]]
) -> YieldPlan:
    """`child_requirements`: (child_product_id, child_target_qty, yield_per_parent) tuples."""
    reqs = [
        YieldChildRequirement(
            child_product_id=cid,
            child_target_qty=target,
            yield_per_parent=yld,
            implied_parent_qty=(target / yld) if yld > 0 else 0.0,
        )
        for cid, target, yld in child_requirements
    ]
    parent_qty = max((r.implied_parent_qty for r in reqs), default=0.0)
    surplus = {r.child_product_id: max(0.0, parent_qty * r.yield_per_parent - r.child_target_qty) for r in reqs}
    return YieldPlan(parent_product_id=parent_product_id, parent_order_qty=parent_qty, children=reqs, surplus=surplus)


def disposition_for_surplus(surplus_qty: float, shelf_life_days: int) -> str:
    """Heuristic: short shelf life needs to move fast (markdown); longer shelf life
    can wait for a merchandising push (hot case)."""
    if surplus_qty <= 0:
        return "none"
    return "markdown" if shelf_life_days <= 3 else "hot case"
