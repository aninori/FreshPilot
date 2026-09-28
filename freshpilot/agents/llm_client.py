"""OpenAI integration for the investigate/narrate LLM nodes.

Provider note (docs/progress.md M6): BRD/CLAUDE.md specify Anthropic; this
project uses OpenAI per an explicit user decision. Provider behavior is covered
by mocked tests; live-provider acceptance is tracked separately.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from freshpilot.agents.budget import BudgetExceeded, RunBudget, complete
from freshpilot.agents.schemas import ExceptionType, Finding
from freshpilot.agents.tools import ToolContext, call_tool
from freshpilot.config import Settings

MAX_TOOL_CALLS = 8  # AGT-4
MAX_SECONDS = 30  # AGT-4

_TOOL_PARAMS: dict[str, dict[str, Any]] = {
    "get_sales_history": {"product_id": {"type": "string"}, "days": {"type": "integer"}},
    "get_inventory": {"product_id": {"type": "string"}},
    "get_forecast": {"product_id": {"type": "string"}},
    "explain_forecast": {"product_id": {"type": "string"}},
    "get_promotions": {"product_id": {"type": "string"}},
    "get_weather": {"dates": {"type": "array", "items": {"type": "string"}}},
    "get_waste_log": {"product_id": {"type": "string"}, "days": {"type": "integer"}},
    "get_catalog_match": {"product_id": {"type": "string"}},
    "simulate_order": {
        "product_id": {"type": "string"},
        "overrides": {"type": "object", "properties": {
            "book_on_hand": {"type": "number", "minimum": 0},
            "on_order_qty": {"type": "number", "minimum": 0},
        }, "additionalProperties": False},
    },
}
_TOOL_REQUIRED: dict[str, list[str]] = {
    "get_sales_history": ["product_id"],
    "get_inventory": ["product_id"],
    "get_forecast": ["product_id"],
    "explain_forecast": ["product_id"],
    "get_promotions": ["product_id"],
    "get_weather": ["dates"],
    "get_waste_log": ["product_id"],
    "get_catalog_match": ["product_id"],
    "simulate_order": ["product_id"],
}

_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": f"Read-only: {name.replace('_', ' ')}.",
            "parameters": {"type": "object", "properties": params, "required": _TOOL_REQUIRED[name]},
        },
    }
    for name, params in _TOOL_PARAMS.items()
]

_SUBMIT_FINDING_SPEC = {
    "type": "function",
    "function": {
        "name": "submit_finding",
        "description": "Submit your final finding for this item, once you have enough evidence.",
        "parameters": Finding.model_json_schema(),
    },
}


def _load_prompt(name: str) -> str:
    return (Path(__file__).parent / "prompts" / f"{name}.txt").read_text()


def investigate_with_llm(
    ctx: ToolContext,
    settings: Settings,
    product_id: str,
    exception_type: ExceptionType,
    error_hint: str | None,
) -> tuple[Finding, list[dict[str, Any]]]:
    if ctx.budget is None:
        ctx.budget = RunBudget(settings.max_run_usd)
    if not ctx.deadline:
        ctx.deadline = time.monotonic() + MAX_SECONDS
    user_msg = f"Investigate product {product_id}. Exception type: {exception_type}."
    if error_hint:
        user_msg += f"\n\nYour previous attempt failed validation: {error_hint}. Fix it and resubmit."
        user_msg += "\nRecorded tool results from this investigation: " + json.dumps(ctx.tool_outputs, default=str)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _load_prompt("investigator")},
        {"role": "user", "content": user_msg},
    ]
    trace: list[dict[str, Any]] = []
    for _ in range(MAX_TOOL_CALLS + 1):
        if time.monotonic() >= ctx.deadline:
            raise BudgetExceeded("Item deadline reached")
        must_submit = ctx.llm_tool_calls >= MAX_TOOL_CALLS
        response = complete(
            settings, ctx.budget, ctx.deadline, messages=messages,
            tools=[_SUBMIT_FINDING_SPEC] if must_submit else [*_TOOL_SPECS, _SUBMIT_FINDING_SPEC],
            tool_choice={"type": "function", "function": {"name": "submit_finding"}}
            if must_submit else "required",
        )
        message = response.choices[0].message
        messages.append(message.model_dump(exclude_none=True))
        if not message.tool_calls:
            break

        for tool_call in message.tool_calls:
            if time.monotonic() >= ctx.deadline:
                raise BudgetExceeded("Item deadline reached")
            name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)
            call_start = time.time()
            if name == "submit_finding":
                finding = Finding.model_validate(args)  # ValidationError propagates to the caller
                trace.append(
                    {
                        "type": "llm", "name": "submit_finding", "summary": finding.summary,
                        "duration_s": round(time.time() - call_start, 4),
                    }
                )
                return finding, trace
            if ctx.llm_tool_calls >= MAX_TOOL_CALLS or time.monotonic() >= ctx.deadline:
                raise BudgetExceeded("Item tool-call or time limit reached")
            if args.get("product_id", product_id) != product_id:
                raise ValueError("Tool call must refer to the investigated product")
            ctx.llm_tool_calls += 1
            result = call_tool(ctx, name, args)
            duration_s = round(time.time() - call_start, 4)
            trace.append({"type": "tool", "name": name, "summary": str(result)[:200], "duration_s": duration_s})
            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": json.dumps(result, default=str)})

    raise RuntimeError(f"LLM investigation for {product_id} did not submit a finding within budget")


def narrate_with_llm(settings: Settings, finding: Finding, ctx: ToolContext) -> str:
    assert ctx.budget is not None
    user_msg = (
        "Rewrite this finding for a store manager: plain language, no jargon, <=60 words, "
        f"using ONLY the numbers already present in it (never invent a new one): {finding.model_dump_json()}"
    )
    response = complete(
        settings, ctx.budget, ctx.deadline,
        messages=[{"role": "system", "content": _load_prompt("narrator")}, {"role": "user", "content": user_msg}],
    )
    return response.choices[0].message.content or finding.summary
