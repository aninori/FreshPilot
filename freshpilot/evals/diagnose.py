"""Read-only delivery-assumption diagnostic; never selects a policy on test data."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

import duckdb
import pandas as pd

from freshpilot.evals.backtest import run_backtest


def run(db_path: Path = Path("data/freshpilot.duckdb")) -> dict[str, Any]:
    with duckdb.connect(str(db_path), read_only=True) as con:
        tables = {name: con.execute(f"SELECT * FROM {name}").df() for name in (
            "products", "sales_daily", "inventory_book", "waste_log", "forecasts", "receipts",
        )}
    report: dict[str, Any] = {}
    scenarios: list[tuple[str, Literal["generated", "daily"], Literal["single_day", "delivery_cycle"]]] = [
        ("generated", "generated", "single_day"), ("daily", "daily", "single_day"),
        ("generated_cycle_baseline", "generated", "delivery_cycle"),
    ]
    for label, mode, baseline in scenarios:
        ledger: list[dict[str, Any]] = []
        results = run_backtest(tables, ledger=ledger, cadence=mode, baseline_horizon=baseline)
        departments = []
        for (policy, dept), rows in pd.DataFrame(ledger).groupby(["policy", "dept_id"]):
            handled = rows["sales"].sum() + rows["waste"].sum()
            departments.append({
                "policy": policy, "department": dept,
                "waste_pct": float(rows["waste"].sum() / handled) if handled else 0.0,
                "stockout_pct": float((rows["lost"] > 0).mean()),
            })
        report[label] = {
            "metrics": {name: asdict(value) for name, value in results.items()},
            "departments": departments,
            "G5_pass": results["freshpilot"].waste_pct < results["baseline"].waste_pct
            and results["freshpilot"].stockout_pct < results["baseline"].stockout_pct,
        }
    path = Path("reports/delivery-diagnostic.json")
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = [
        "# Delivery-assumption diagnostic", "",
        "The generated-cadence case follows the synthetic receipt schedule. The daily case is a sensitivity check,",
        "not a selected or tuned policy. Both policies share each scenario's receipt opportunities and waste process.",
        "Targets are unscaled calibrated newsvendor quantiles; bakery supply expires on the same day.", "",
        "The cycle-baseline scenario sums last week's sales across the delivery horizon. This is a proposed",
        "benchmark correction, not the currently specified BRD baseline and not the main G5 gate.", "",
        "| Scenario | Policy | Waste | Stockouts | Margin |", "|---|---|---:|---:|---:|",
    ]
    for scenario, data in report.items():
        for name, m in data["metrics"].items():
            lines.append(
                f"| {scenario} | {name} | {m['waste_pct']:.2%} | "
                f"{m['stockout_pct']:.2%} | ${m['margin_usd']:,.2f} |"
            )
    lines += [
        "", "## Limits", "",
        "- Supplier weekday schedules and lead times are not connected to the generator's department cadences.",
        "- The baseline uses last week's same-day simulated sales minus book inventory, exactly as specified;",
        "  it does not sum several days of demand on an infrequent delivery day.",
        "- Multi-day forecasts use a square-root-of-time approximation, not independently calibrated horizon models.",
        "- Sales forecasts are rolling historical predictions, not retrained on either counterfactual sales path.",
        "- Waste rates are fixed from pre-test history; FIFO expiry for multi-day goods is not modeled.",
        "- No goodwill, cost parameter, or forecast multiplier was fitted to these test results.",
    ]
    Path("reports/delivery-diagnostic.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
