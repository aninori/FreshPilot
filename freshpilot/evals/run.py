"""Entry point for `make eval` (EVAL-1): assembles G1-G8 into reports/eval.json
and reports/eval.md. G7 (faithfulness) and G8 (LLM cost) need the agents/guardrails
from M6, not built yet -- marked pending until then.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from freshpilot.catalog.run import run as run_catalog
from freshpilot.config import SEED
from freshpilot.evals.backtest import run_backtest
from freshpilot.evals.phantom_metrics import compute_phantom_metrics
from freshpilot.exceptions.run import run as run_exceptions
from freshpilot.forecasting.run import run as run_forecasting

DB_PATH = Path("data") / "freshpilot.duckdb"
REPORTS_DIR = Path("reports")


def _load_tables(db_path: Path, names: list[str]) -> dict[str, pd.DataFrame]:
    con = duckdb.connect(str(db_path))
    try:
        tables = {name: con.execute(f"SELECT * FROM {name}").df() for name in names}
    finally:
        con.close()
    for df in tables.values():
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
    return tables


def run(db_path: Path = DB_PATH, seed: int = SEED) -> dict[str, Any]:
    # Each of these both persists its own pipeline-stage tables (forecasts,
    # match_candidates/product_listing_map, exceptions/item_reasons) and
    # returns the summary metrics needed here — running them is what `make
    # train`'s two steps plus the exception-check stage already do.
    catalog_metrics = run_catalog(db_path=db_path, seed=seed)
    forecasting_result = run_forecasting(db_path=db_path, seed=seed)
    exceptions_metrics = run_exceptions(db_path=db_path, seed=seed)

    tables = _load_tables(
        db_path, ["products", "answer_key", "inventory_book", "waste_log", "sales_daily", "forecasts"]
    )
    demo_date = tables["forecasts"]["date"].max()
    phantom_metrics = compute_phantom_metrics(
        tables["products"], tables["answer_key"], tables["inventory_book"],
        tables["waste_log"], tables["sales_daily"], tables["forecasts"], demo_date,
    )

    backtest_tables = {k: tables[k] for k in ("products", "sales_daily", "inventory_book", "waste_log", "forecasts")}
    backtest_results = run_backtest(backtest_tables)
    fp, base = backtest_results["freshpilot"], backtest_results["baseline"]
    g5_pass = fp.waste_pct < base.waste_pct and fp.stockout_pct < base.stockout_pct

    report: dict[str, Any] = {
        "seed": seed,
        "G1_wape_improvement": {
            "value": forecasting_result["improvement"], "target": ">=0.25",
            "pass": forecasting_result["improvement"] >= 0.25,
        },
        "G2_coverage_80pct": {
            "value": forecasting_result["coverage"], "target": "[0.78, 0.85]",
            "pass": 0.78 <= forecasting_result["coverage"] <= 0.85,
        },
        "G3_phantom_precision_recall": {
            "precision": phantom_metrics.precision, "recall": phantom_metrics.recall,
            "target": "recall>=0.90, precision>=0.80",
            "pass": phantom_metrics.recall >= 0.90 and phantom_metrics.precision >= 0.80,
        },
        "G4_match_f1": {"value": catalog_metrics["f1"], "target": ">=0.93", "pass": catalog_metrics["f1"] >= 0.93},
        "G5_waste_and_stockout_vs_baseline": {
            "freshpilot": {"waste_pct": fp.waste_pct, "stockout_pct": fp.stockout_pct},
            "baseline": {"waste_pct": base.waste_pct, "stockout_pct": base.stockout_pct},
            "target": "both below baseline",
            "pass": g5_pass,
        },
        "G6_escalation_rate": {
            "value": exceptions_metrics["escalation_rate"], "target": "<=0.01",
            "pass": exceptions_metrics["escalation_rate"] <= 0.01,
        },
        "G7_faithfulness": {"status": "pending: needs M6 guardrails"},
        "G8_llm_cost": {"status": "pending: needs M6 agents"},
    }

    REPORTS_DIR.mkdir(exist_ok=True)
    (REPORTS_DIR / "eval.json").write_text(json.dumps(report, indent=2, default=str))
    (REPORTS_DIR / "eval.md").write_text(_render_markdown(report))
    return report


def _render_markdown(report: dict[str, Any]) -> str:
    lines = ["# FreshPilot evaluation report", "", f"SEED={report['seed']}", ""]
    for key, val in report.items():
        if key == "seed":
            continue
        status = "pending" if "status" in val else ("PASS" if val["pass"] else "FAIL")
        lines.append(f"## {key}: {status}")
        lines.append("```json")
        lines.append(json.dumps(val, indent=2, default=str))
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    report = run()
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
