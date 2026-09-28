"""Persist evaluated findings, calculations and guardrail events (ORD-6/GRD-7)."""

from __future__ import annotations

import json
from typing import Any

import duckdb

from freshpilot.data.schema import DDL


def persist_evaluated_findings(con: duckdb.DuckDBPyConnection, date: str, outputs: list[dict[str, Any]]) -> None:
    for name in ("findings", "calculations", "guardrail_events"):
        con.execute(DDL[name].replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1))
    prefix = f"eval:{date}:"
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute("DELETE FROM findings WHERE date = ?", [date])
        con.execute("DELETE FROM guardrail_events WHERE starts_with(run_id, ?)", [prefix])
        for result in outputs:
            pid, kind = result["product_id"], result["exception_type"]
            con.execute("INSERT INTO findings VALUES (?, ?, ?, ?, ?, ?)", [
                pid, date, kind, json.dumps(result["finding"]), result["route"], result["source"],
            ])
            for calc in result["calculations"].values():
                con.execute("INSERT INTO calculations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING", [
                    calc["calc_id"], pid, calc.get("calc_type", "order"), date,
                    calc["target_qty"], calc["estimated_on_hand"],
                    calc["order_cases"], calc["order_units"], json.dumps(calc["breakdown"]),
                ])
            for event in result["guardrail_events"]:
                con.execute("INSERT INTO guardrail_events VALUES (?, ?, ?, ?, ?, ?, ?)", [
                    f"{prefix}{pid}:{kind}", pid, event["type"], event["passed"],
                    event["before_value"], event["after_value"], event["details"],
                ])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
