"""LangGraph investigator with trusted tool provenance and fail-closed routing."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from freshpilot.agents.budget import RunBudget
from freshpilot.agents.fallback import build_fallback_finding
from freshpilot.agents.llm_client import MAX_SECONDS, investigate_with_llm, narrate_with_llm
from freshpilot.agents.schemas import ExceptionType, Finding
from freshpilot.agents.tools import ToolContext, simulate_order
from freshpilot.config import Settings
from freshpilot.guardrails.routing import Route
from freshpilot.guardrails.routing import route as grd_route
from freshpilot.guardrails.validation import validate_finding

MAX_RETRIES = 1


class InvestigatorState(TypedDict):
    product_id: str
    exception_type: ExceptionType
    finding: Finding | None
    validation_errors: list[str]
    retry_count: int
    guardrail_fired: bool
    route: Route | None
    source: str
    trace: list[dict[str, Any]]
    guardrail_events: list[dict[str, Any]]
    tool_outputs: list[dict[str, Any]]
    calculations: dict[str, dict[str, Any]]
    llm_usage: list[dict[str, Any]]


def _record(state: InvestigatorState, step_type: str, name: str, summary: str, duration_s: float) -> None:
    state["trace"].append(
        {"type": step_type, "name": name, "summary": summary[:200], "duration_s": round(duration_s, 4)}
    )


def _failure(state: InvestigatorState, errors: list[str]) -> None:
    state["guardrail_fired"] = True
    for error in errors:
        state["guardrail_events"].append({
            "type": error.split(":")[0], "passed": False, "details": error,
            "before_value": None, "after_value": None,
        })


def build_graph(ctx: ToolContext, settings: Settings) -> Any:
    base: dict[str, Any] = {}
    capacity: float | None = None

    def errors_for(state: InvestigatorState) -> list[str]:
        finding = state["finding"]
        if finding is None:
            return ["schema: no valid finding"]
        return validate_finding(
            finding, state["product_id"], state["exception_type"], ctx.tool_outputs,
            ctx.calculations, base, capacity,
        )

    def use_fallback(state: InvestigatorState) -> None:
        state["finding"] = build_fallback_finding(ctx, state["product_id"], state["exception_type"])
        state["source"] = "fallback"
        errors = errors_for(state)
        if errors:
            raise ValueError("Deterministic fallback failed validation: " + "; ".join(errors))
        state["validation_errors"] = []

    def triage(state: InvestigatorState) -> InvestigatorState:
        nonlocal base, capacity
        t0 = time.monotonic()
        base = simulate_order(ctx, state["product_id"])
        product = ctx.products[ctx.products["product_id"] == state["product_id"]].iloc[0]
        raw_capacity = product.get("max_shelf_capacity")
        capacity = float(raw_capacity) if raw_capacity is not None else None
        _record(state, "rules", "triage", state["exception_type"], time.monotonic() - t0)
        return state

    def investigate(state: InvestigatorState) -> InvestigatorState:
        t0 = time.monotonic()
        state["retry_count"] += 1
        if settings.llm_enabled:
            try:
                finding, tool_trace = investigate_with_llm(
                    ctx, settings, state["product_id"], state["exception_type"],
                    "; ".join(state["validation_errors"]) or None,
                )
                state["trace"].extend(tool_trace)
                state["source"] = "llm"
                state["finding"] = finding
            except Exception as exc:
                # Never place SDK exception strings (which may contain request
                # headers or secrets) in logs or manager-facing output.
                error = f"llm_failure: {type(exc).__name__}"
                _failure(state, [error])
                state["finding"] = None
                state["validation_errors"] = [error]
                state["source"] = "fallback"
        else:
            use_fallback(state)
        _record(state, state["source"], "investigate", state["exception_type"], time.monotonic() - t0)
        return state

    def validate(state: InvestigatorState) -> InvestigatorState:
        t0 = time.monotonic()
        errors = errors_for(state)
        state["validation_errors"] = errors
        if errors:
            _failure(state, errors)
            if state["finding"] is not None:
                calc = ctx.calculations.get(state["finding"].recommended_calc_id or "", {})
                for event in state["guardrail_events"][-len(errors):]:
                    if event["type"] == "policy_band":
                        event["before_value"] = calc.get("order_units")
                        event["after_value"] = base["order_units"]
        _record(state, "rules", "validate", "; ".join(errors) or "ok", time.monotonic() - t0)
        return state

    def should_retry(state: InvestigatorState) -> str:
        if state["validation_errors"] and state["retry_count"] <= MAX_RETRIES and time.monotonic() < ctx.deadline:
            return "investigate"
        if state["validation_errors"]:
            return "fallback"
        return "route"

    def fallback_node(state: InvestigatorState) -> InvestigatorState:
        use_fallback(state)
        return state

    def route_node(state: InvestigatorState) -> InvestigatorState:
        finding = state["finding"]
        assert finding is not None
        state["route"] = grd_route(finding, state["guardrail_fired"])
        _record(state, "rules", "route", str(state["route"]), 0.0)
        return state

    def narrate(state: InvestigatorState) -> InvestigatorState:
        t0 = time.monotonic()
        finding = state["finding"]
        assert finding is not None
        if settings.llm_enabled and state["source"] == "llm":
            original = finding.summary
            for attempt in range(MAX_RETRIES + 1):
                try:
                    polished = narrate_with_llm(settings, finding, ctx)
                    finding.summary = polished
                    errors = errors_for(state)
                except Exception as exc:
                    errors = [f"narration_failure: {type(exc).__name__}"]
                if not errors:
                    break
                finding.summary = original
                _failure(state, errors)
                if attempt == MAX_RETRIES or time.monotonic() >= ctx.deadline:
                    use_fallback(state)
                    break
        # Narration runs after route in the BRD graph. Its failures must still
        # update the final route; otherwise a late violation could auto-approve.
        assert state["finding"] is not None
        state["route"] = grd_route(state["finding"], state["guardrail_fired"])
        _record(state, "rules", "narrate", state["finding"].summary, time.monotonic() - t0)
        return state

    graph = StateGraph(InvestigatorState)
    for name, node in (
        ("triage", triage), ("investigate", investigate), ("validate", validate),
        ("route", route_node), ("narrate", narrate),
        ("fallback", fallback_node),
    ):
        graph.add_node(name, node)
    graph.set_entry_point("triage")
    graph.add_edge("triage", "investigate")
    graph.add_edge("investigate", "validate")
    graph.add_conditional_edges(
        "validate", should_retry, {"investigate": "investigate", "route": "route", "fallback": "fallback"},
    )
    graph.add_edge("fallback", "route")
    graph.add_edge("route", "narrate")
    graph.add_edge("narrate", END)
    return graph.compile()


def run_investigator(
    ctx: ToolContext, settings: Settings, product_id: str, exception_type: ExceptionType
) -> InvestigatorState:
    if ctx.budget is None:
        ctx.budget = RunBudget(settings.max_run_usd)
    # Each item gets a fresh evidence ledger and deadline, but shares run spend.
    local = replace(ctx, tool_outputs=[], calculations={}, deadline=time.monotonic() + MAX_SECONDS, llm_tool_calls=0)
    usage_start = len(ctx.budget.calls)
    initial: InvestigatorState = {
        "product_id": product_id, "exception_type": exception_type, "finding": None,
        "validation_errors": [], "retry_count": 0, "guardrail_fired": False,
        "route": None, "source": "fallback", "trace": [], "guardrail_events": [],
        "tool_outputs": [], "calculations": {}, "llm_usage": [],
    }
    result: InvestigatorState = build_graph(local, settings).invoke(initial)
    result["tool_outputs"] = local.tool_outputs
    result["calculations"] = local.calculations
    result["llm_usage"] = ctx.budget.calls[usage_start:]
    return result
