"""EXC-6: a one-line, manager-facing reason and category for every item — including
the ones that passed every check. Phrasing matches docs/ui-reference.html's Agent
review screen ("Enough on the shelf until next delivery", "Demand steady, usual
amount", "Warm weekend · ordered 13% more than usual").
"""

from __future__ import annotations

CATEGORIES = ("normal", "adjusted", "no_order", "data_fix")


def reason_for_item(
    exception_types: list[str],
    order_cases: int,
    pct_change_vs_usual: float | None,
    top_driver_label: str | None,
) -> tuple[str, str]:
    if "phantom_stock" in exception_types:
        return "Shelf likely empty", "data_fix"
    if "shrink_spike" in exception_types:
        return "Spoiling faster than usual", "data_fix"
    if "low_confidence_match" in exception_types:
        return "Catalog match needs review", "data_fix"

    if order_cases == 0:
        return "Enough on the shelf until next delivery", "no_order"

    if top_driver_label and pct_change_vs_usual is not None and abs(pct_change_vs_usual) >= 0.10:
        direction = "more" if pct_change_vs_usual > 0 else "less"
        return f"{top_driver_label} · ordered {abs(pct_change_vs_usual):.0%} {direction} than usual", "adjusted"

    plan_change_types = {"order_swing", "bake_plan_change", "yield_plan_change"}
    if plan_change_types & set(exception_types) and pct_change_vs_usual is not None:
        direction = "more" if pct_change_vs_usual > 0 else "less"
        return f"Ordered {abs(pct_change_vs_usual):.0%} {direction} than usual · no clear reason", "adjusted"

    return "Demand steady, usual amount", "normal"
