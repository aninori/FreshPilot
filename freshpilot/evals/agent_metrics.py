"""EVAL-3: measure actual fallback routing and verified output, not flag counts."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import pandas as pd

from freshpilot.agents.graph import run_investigator
from freshpilot.agents.schemas import ExceptionType, Finding
from freshpilot.agents.tools import ToolContext
from freshpilot.config import Settings
from freshpilot.guardrails.faithfulness import is_faithful
from freshpilot.guardrails.validation import numeric_values


def evaluate_agents(tables: dict[str, pd.DataFrame], demo_date: pd.Timestamp, settings: Settings) -> dict[str, Any]:
    settings = replace(settings, llm_mode="off")
    ctx = ToolContext(
        **{name: tables[name] for name in (
            "products", "sales_daily", "inventory_book", "waste_log", "forecasts", "promotions", "weather",
            "calendar", "product_listing_map", "match_candidates",
        )}, demo_date=demo_date,
    )
    flags = tables["exceptions"]
    flags = flags[flags["date"] == demo_date]
    escalated: set[str] = set()
    schema_valid = faithful = 0
    outputs: list[dict[str, Any]] = []
    for row in flags.itertuples(index=False):
        result = run_investigator(ctx, settings, row.product_id, cast(ExceptionType, row.type))
        finding = result["finding"]
        assert finding is not None
        Finding.model_validate(finding.model_dump())
        schema_valid += 1
        text = " ".join([finding.summary, *finding.reasons, finding.precondition or ""])
        known = numeric_values([tool["result"] for tool in result["tool_outputs"]])
        faithful += int(is_faithful(text, known)[0])
        if result["route"] == "escalate":
            escalated.add(row.product_id)
        outputs.append({
            "product_id": row.product_id, "exception_type": row.type, "finding": finding.model_dump(),
            "route": result["route"], "source": result["source"],
            "guardrail_events": result["guardrail_events"], "calculations": result["calculations"],
            "tool_outputs": result["tool_outputs"], "trace": result["trace"],
        })
    n = len(outputs)
    return {
        "mode": "fallback", "n_findings": n, "n_escalated": len(escalated),
        "escalation_rate": len(escalated) / len(tables["products"]),
        "schema_validity": schema_valid / n if n else None,
        "faithfulness": faithful / n if n else None,
        "llm_touched_fraction": 0.0, "llm_cost_usd": 0.0,
        "average_tool_calls": sum(len(r["tool_outputs"]) for r in outputs) / n if n else 0.0,
        "outputs": outputs,
    }
