"""Local run, stage, trace and manager-action storage (OBS-1..3)."""

from __future__ import annotations

import json
from typing import Any

import duckdb

SCHEMA = {
    "pipeline_runs": "run_id VARCHAR PRIMARY KEY, date DATE, started_at TIMESTAMP, duration_s DOUBLE, "
                     "status VARCHAR, mode VARCHAR, error VARCHAR, summary VARCHAR",
    "pipeline_stages": "run_id VARCHAR, ordinal INTEGER, name VARCHAR, executor VARCHAR, "
                       "started_at TIMESTAMP, duration_s DOUBLE, status VARCHAR, count INTEGER, details VARCHAR",
    "traces": "run_id VARCHAR PRIMARY KEY, pipeline_run_id VARCHAR, agent VARCHAR, product_id VARCHAR, "
              "outcome VARCHAR, tokens_in BIGINT, tokens_out BIGINT, cost_usd DOUBLE, payload VARCHAR",
    "run_items": "run_id VARCHAR, product_id VARCHAR, dept_id VARCHAR, category VARCHAR, payload VARCHAR, "
                 "PRIMARY KEY(run_id, product_id)",
    "orders": "run_id VARCHAR, product_id VARCHAR, calc_id VARCHAR, cases INTEGER, units DOUBLE, "
              "value DOUBLE, status VARCHAR, PRIMARY KEY(run_id, product_id)",
    "decisions": "id VARCHAR PRIMARY KEY, run_id VARCHAR, product_id VARCHAR, status VARCHAR, "
                 "recommended_cases INTEGER, cases INTEGER, payload VARCHAR",
    "match_reviews": "id VARCHAR PRIMARY KEY, run_id VARCHAR, listing_id VARCHAR, product_id VARCHAR, "
                     "status VARCHAR, payload VARCHAR",
    "inventory_estimates": "run_id VARCHAR, product_id VARCHAR, book_on_hand DOUBLE, "
                           "estimated_on_hand DOUBLE, phantom BOOLEAN, calc_id VARCHAR",
    "human_events": "id VARCHAR PRIMARY KEY, run_id VARCHAR, created_at TIMESTAMP, action VARCHAR, "
                    "subject VARCHAR, before_value VARCHAR, after_value VARCHAR, reason VARCHAR",
    "model_health": "run_id VARCHAR PRIMARY KEY, payload VARCHAR",
    "order_overrides": "run_id VARCHAR, product_id VARCHAR, action VARCHAR, original_cases INTEGER, "
                       "manager_cases INTEGER, reason VARCHAR, note VARCHAR, counted_qty DOUBLE, "
                       "created_at TIMESTAMP, PRIMARY KEY(run_id, product_id)",
    "department_controls": "run_id VARCHAR, dept_id VARCHAR, auto_approve BOOLEAN, updated_at TIMESTAMP, "
                           "PRIMARY KEY(run_id, dept_id)",
    "order_batches": "run_id VARCHAR PRIMARY KEY, sent_at TIMESTAMP, recall_until TIMESTAMP, recalled_at TIMESTAMP",
    "manager_feedback": "id VARCHAR PRIMARY KEY, run_id VARCHAR, product_id VARCHAR, dept_id VARCHAR, "
                        "category VARCHAR, action VARCHAR, agent_cases INTEGER, manager_cases INTEGER, "
                        "reason VARCHAR, note VARCHAR, created_at TIMESTAMP, evaluation_candidate BOOLEAN, "
                        "training_candidate BOOLEAN",
}


def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    for name, columns in SCHEMA.items():
        con.execute(f"CREATE TABLE IF NOT EXISTS {name} ({columns})")


def dump(value: Any) -> str:
    return json.dumps(value, default=str, allow_nan=False)


def store_trace(con: duckdb.DuckDBPyConnection, pipeline_id: str, trace_id: str,
                product_id: str, result: dict[str, Any], agent: str = "investigator") -> None:
    usage = result.get("llm_usage", [])
    con.execute("INSERT INTO traces VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [
        trace_id, pipeline_id, agent, product_id, result.get("route", "review"),
        sum(c.get("tokens_in", 0) for c in usage), sum(c.get("tokens_out", 0) for c in usage),
        sum(c.get("cost_usd", 0) for c in usage), dump(result),
    ])
