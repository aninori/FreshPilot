"""M6 acceptance against real demo inputs, optionally including a bounded live case."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from freshpilot.agents.graph import run_investigator
from freshpilot.agents.tools import ToolContext
from freshpilot.config import load_settings
from freshpilot.data.migrate import migrate
from freshpilot.evals.agent_metrics import evaluate_agents


def run(live: bool = False, max_usd: float = 0.03) -> dict[str, Any]:
    names = ("products", "sales_daily", "inventory_book", "waste_log", "forecasts", "promotions",
             "weather", "calendar", "product_listing_map", "match_candidates", "exceptions")
    with duckdb.connect("data/freshpilot.duckdb") as con:
        migrate(con)
        tables = {name: con.execute("SELECT * FROM " + name).df() for name in names}
    settings = replace(load_settings(), llm_mode="on" if live else "off", max_run_usd=max_usd)
    date = pd.Timestamp(settings.demo_date or tables["forecasts"]["date"].max())
    measured = evaluate_agents(tables, date, settings)
    report = {k: v for k, v in measured.items() if k != "outputs"}
    if live:
        ctx = ToolContext(**{name: tables[name] for name in names if name != "exceptions"}, demo_date=date)
        pid = str(tables["exceptions"].query("type == 'phantom_stock'").iloc[0]["product_id"])
        result = run_investigator(ctx, settings, pid, "phantom_stock")
        assert result["finding"] is not None and ctx.budget is not None
        report["live"] = {**result, "finding": result["finding"].model_dump()}
        report["live_cost_usd"] = ctx.budget.cost_usd
        report["live_verified"] = any(c["status"] == "complete" for c in result["llm_usage"])
        # Successful fallback is valid; failures must route to a human.
        report["pass"] = report["live_verified"] and result["route"] == "escalate"
    else:
        report["pass"] = report["schema_validity"] == report["faithfulness"] == 1.0
    Path("reports").mkdir(exist_ok=True)
    Path("reports/agent-acceptance.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-usd", type=float, default=0.03)
    args = parser.parse_args()
    result = run(args.live, args.max_usd)
    print(json.dumps({k: v for k, v in result.items() if k != "live"}, indent=2))
    raise SystemExit(0 if result["pass"] else 1)
