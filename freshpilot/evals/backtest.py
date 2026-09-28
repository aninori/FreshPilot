"""EVAL-2: paired simulation with separate physical and book inventory.

Only the simulator sees true stock; decisions use book/estimated stock.
Waste rates are frozen before the holdout. Orders arrive on scheduled receipt
days (or daily in an explicit sensitivity scenario). Lead times and FIFO expiry
of multi-day products are not modeled. Bakery stock expires every night.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd

from freshpilot.engine.calculations import compute_order, round_up_to_case_pack
from freshpilot.engine.inventory import estimate_inventory, trailing_waste_rate
from freshpilot.engine.newsvendor import compute_critical_ratio, target_quantile
from freshpilot.forecasting.horizon import calibrated_quantile_frame, cycle_days_for
from freshpilot.forecasting.model import QUANTILES

TEST_DAYS = 28


@dataclass(frozen=True)
class BacktestMetrics:
    policy: str
    total_true_demand: float
    total_sales: float
    total_waste: float
    total_lost_sales_units: float
    stockout_events: int
    total_product_days: int
    waste_pct: float
    stockout_pct: float
    lost_sales_usd: float
    margin_usd: float
    initial_stock: float
    total_ordered: float
    ending_stock: float


def settle_day(stock: float, order: float, demand: float, waste_rate: float) -> tuple[float, float, float, float]:
    """Conserve physical stock: opening + receipts = sales + waste + closing."""
    available = stock + order
    sales = min(demand, available)
    waste = min(available - sales, available * min(max(waste_rate, 0.0), 1.0))
    return sales, waste, demand - sales, available - sales - waste


def run_backtest(
    tables: dict[str, pd.DataFrame], ledger: list[dict[str, Any]] | None = None,
    cadence: Literal["daily", "generated"] = "generated",
    baseline_horizon: Literal["single_day", "delivery_cycle"] = "single_day",
) -> dict[str, BacktestMetrics]:
    frames = {name: frame.copy() for name, frame in tables.items()}
    for frame in frames.values():
        if "date" in frame:
            frame["date"] = pd.to_datetime(frame["date"])
    dates = sorted(frames["sales_daily"]["date"].unique())[-TEST_DAYS:]
    if len(dates) != TEST_DAYS:
        raise ValueError("Backtest requires 28 held-out days")
    cutoff = pd.Timestamp(dates[0]) - pd.Timedelta(days=1)
    anchor = frames["sales_daily"]["date"].min()
    grouped = {
        name: dict(tuple(frames[name].groupby("product_id")))
        for name in ("sales_daily", "inventory_book", "waste_log", "forecasts")
    }
    totals: dict[str, dict[str, float]] = {
        policy: dict.fromkeys(
            ["demand", "sales", "waste", "lost", "stockouts", "lost_usd", "margin", "initial", "ordered", "ending"],
            0.0,
        ) for policy in ("freshpilot", "baseline")
    }
    for product_index, product in enumerate(frames["products"].itertuples(index=False)):
        pid = product.product_id
        history = grouped["sales_daily"][pid].set_index("date").sort_index()
        inv = grouped["inventory_book"][pid]
        waste = grouped["waste_log"].get(pid, frames["waste_log"].iloc[:0])
        forecasts = grouped["forecasts"][pid].set_index("date")
        initial = inv[inv["date"] <= cutoff].sort_values("date")
        if initial.empty or any(d not in forecasts.index for d in dates):
            raise ValueError(f"Missing opening inventory or holdout forecasts for {pid}")
        physical_start = float(initial.iloc[-1]["true_on_hand"])
        book_start = float(initial.iloc[-1]["book_on_hand"])
        cycle = 1 if cadence == "daily" else cycle_days_for(product.dept_id)
        # Recover the phase from pre-holdout receipts, never future receipts.
        # The generator uses the product index as phase when no receipt exists.
        phase = product_index % cycle
        if "receipts" in frames and cycle > 1:
            receipts = frames["receipts"]
            past = receipts[(receipts["product_id"] == pid) & (receipts["date"] <= cutoff)]
            if not past.empty:
                phase = int((past["date"].max() - anchor).days) % cycle
        wr = trailing_waste_rate(waste, inv, pid, cutoff)
        shelf_life = 1 if product.dept_id == "bakery" else product.shelf_life_days
        cr = compute_critical_ratio(
            product.unit_cost, product.unit_price, product.salvage_value, shelf_life, wr,
        )
        targets: dict[pd.Timestamp, tuple[float, float]] = {}
        daily = calibrated_quantile_frame(forecasts.loc[dates])
        median = daily[:, QUANTILES.index(0.5)]
        horizon = np.maximum(0, median[:, None] * cycle + (daily - median[:, None]) * np.sqrt(cycle))
        for i, date in enumerate(dates):
            curve = dict(zip(QUANTILES, horizon[i], strict=True))
            targets[date] = (target_quantile(curve, cr.cr), float(median[i]))

        for policy, total in totals.items():
            stock, book = physical_start, book_start
            total["initial"] += stock
            sales_history = {d: float(v) for d, v in history.loc[:cutoff, "qty_sold"].items()}
            zeros = 0
            for qty in reversed(list(sales_history.values())):
                if qty != 0:
                    break
                zeros += 1
            for date in dates:
                demand = float(history.loc[date, "true_demand"])
                due = (pd.Timestamp(date) - anchor).days % cycle == phase
                if not due:
                    order = 0.0
                elif policy == "freshpilot":
                    target, mean = targets[date]
                    estimate = estimate_inventory(shelf_life, book, mean, wr, zeros)
                    calc = compute_order(
                        pid, "backtest", str(pd.Timestamp(date).date()), target, estimate.estimated_on_hand,
                        0.0, 1 if product.dept_id == "bakery" else product.case_pack,
                        product.min_order_cases, product.max_order_cases,
                        {"cr": cr.cr, "book_on_hand": book},
                    )
                    order = calc.order_units
                else:
                    # The BRD baseline is single-day. The cycle variant is an
                    # explicit diagnostic, not a silent change to that benchmark.
                    horizon_days = cycle if baseline_horizon == "delivery_cycle" else 1
                    previous = sum(
                        sales_history.get(date + pd.Timedelta(days=offset - 7), 0.0)
                        for offset in range(horizon_days)
                    )
                    pack = 1 if product.dept_id == "bakery" else product.case_pack
                    cases = round_up_to_case_pack(max(0.0, previous - book), pack)
                    order = float(cases * pack)
                opening, opening_book = stock, book
                sales, wasted, lost, stock = settle_day(stock, order, demand, wr)
                if shelf_life == 1:
                    # ORD-5: same-day goods cannot carry into tomorrow's supply.
                    wasted += stock
                    stock = 0.0
                book = max(0.0, book + order - sales - wasted)
                sales_history[date] = sales
                zeros = zeros + 1 if sales == 0 else 0
                total["demand"] += demand
                total["sales"] += sales
                total["waste"] += wasted
                total["lost"] += lost
                total["stockouts"] += int(lost > 0)
                total["lost_usd"] += lost * product.unit_price
                total["margin"] += sales * (product.unit_price - product.unit_cost)
                total["margin"] -= wasted * (product.unit_cost - product.salvage_value)
                total["ordered"] += order
                if ledger is not None:
                    ledger.append({
                        "policy": policy, "product_id": pid, "dept_id": product.dept_id, "date": str(date),
                        "opening_stock": opening, "book_on_hand": opening_book, "ordered": order,
                        "sales": sales, "waste": wasted, "lost": lost, "ending_stock": stock,
                        "delivery_due": due, "cycle_days": cycle,
                    })
            total["ending"] += stock
    days = len(frames["products"]) * len(dates)
    return {
        policy: BacktestMetrics(
            policy, t["demand"], t["sales"], t["waste"], t["lost"], int(t["stockouts"]), days,
            t["waste"] / (t["sales"] + t["waste"]) if t["sales"] + t["waste"] else 0.0,
            t["stockouts"] / days if days else 0.0, t["lost_usd"], t["margin"],
            t["initial"], t["ordered"], t["ending"],
        ) for policy, t in totals.items()
    }
