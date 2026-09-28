"""AGT-3/4 and GRD-1/2/3/5: adversarial integration cases, without a live API."""

from dataclasses import replace
from types import SimpleNamespace

import pandas as pd
import pytest
from freshpilot.agents import graph, llm_client
from freshpilot.agents.budget import BudgetExceeded, RunBudget, complete
from freshpilot.agents.fallback import build_fallback_finding
from freshpilot.agents.schemas import Evidence
from freshpilot.agents.tools import ToolContext, call_tool
from freshpilot.config import Settings
from freshpilot.forecasting.model import QUANTILE_COLUMNS


@pytest.fixture
def small_ctx():
    dates = pd.date_range("2025-06-01", periods=30)
    return ToolContext(
        products=pd.DataFrame([{
            "product_id": "P1", "dept_id": "produce", "shelf_life_days": 7,
            "unit_cost": 2.0, "unit_price": 4.0, "salvage_value": 0.0,
            "case_pack": 6, "min_order_cases": 0, "max_order_cases": 100,
        }]),
        sales_daily=pd.DataFrame({"product_id": "P1", "date": dates, "qty_sold": 10.0, "on_promo": False}),
        inventory_book=pd.DataFrame({"product_id": "P1", "date": dates, "book_on_hand": 0.0}),
        waste_log=pd.DataFrame({"product_id": "P1", "date": dates, "qty_wasted": 0.0, "reason": "spoilage"}),
        forecasts=pd.DataFrame({
            "product_id": "P1", "date": dates, **dict.fromkeys(QUANTILE_COLUMNS, 20.0),
            "q10_cal": 10.0, "q90_cal": 30.0,
        }),
        promotions=pd.DataFrame(), weather=pd.DataFrame(), calendar=pd.DataFrame(),
        product_listing_map=pd.DataFrame(), match_candidates=pd.DataFrame(), demo_date=dates[-1],
    )


@pytest.fixture
def enabled():
    return Settings(42, "test-not-a-secret", "gpt-4o-mini", "on", 1.0, None, None, None, None)


def test_ORD5_bakery_simulation_uses_daily_units_with_calculation_provenance(small_ctx):
    small_ctx.products.loc[0, "dept_id"] = "bakery"
    result = call_tool(small_ctx, "simulate_order", {"product_id": "P1"})
    assert result["calc_type"] == "bake"
    assert result["quantity_unit"] == "units"
    assert result["breakdown"]["cycle_days"] == 1
    assert result["breakdown"]["case_pack"] == 1
    assert result["calc_id"] in small_ctx.calculations


def test_GRD1_fabricated_calc_id_is_rejected_and_failure_survives_fallback(monkeypatch, small_ctx, enabled):
    def malicious(ctx, settings, pid, kind, hint):
        finding = build_fallback_finding(ctx, pid, kind)
        finding.recommended_calc_id = "CALC-invented"
        return finding, []
    monkeypatch.setattr(graph, "investigate_with_llm", malicious)
    result = graph.run_investigator(small_ctx, enabled, "P1", "order_swing")
    assert result["source"] == "fallback"
    assert result["route"] == "escalate"
    assert result["guardrail_fired"]
    assert any(e["type"] == "provenance" for e in result["guardrail_events"])


def test_GRD3_self_reported_evidence_cannot_authorize_invented_numbers(monkeypatch, small_ctx, enabled):
    def malicious(ctx, settings, pid, kind, hint):
        finding = build_fallback_finding(ctx, pid, kind)
        finding.summary = "Order 99999 cases."
        finding.evidence.append(Evidence(value=99999, unit="cases", source_tool="simulate_order",
                                         source_ref=finding.recommended_calc_id))
        return finding, []
    monkeypatch.setattr(graph, "investigate_with_llm", malicious)
    result = graph.run_investigator(small_ctx, enabled, "P1", "order_swing")
    assert "99999" not in result["finding"].summary
    assert result["route"] == "escalate"
    assert any(e["type"] == "faithfulness" for e in result["guardrail_events"])


def test_GRD2_real_calculation_outside_policy_band_is_blocked(monkeypatch, small_ctx, enabled):
    def malicious(ctx, settings, pid, kind, hint):
        finding = build_fallback_finding(ctx, pid, kind)
        alternate = call_tool(ctx, "simulate_order", {"product_id": pid, "overrides": {"on_order_qty": 1000}})
        finding.recommended_calc_id = alternate["calc_id"]
        return finding, []
    monkeypatch.setattr(graph, "investigate_with_llm", malicious)
    result = graph.run_investigator(small_ctx, enabled, "P1", "order_swing")
    assert result["route"] == "escalate"
    assert any(e["type"] == "policy_band" for e in result["guardrail_events"])


def test_AGT6_narrator_failure_falls_back_and_escalates(monkeypatch, small_ctx, enabled):
    monkeypatch.setattr(graph, "investigate_with_llm", lambda ctx, s, p, k, h: (build_fallback_finding(ctx, p, k), []))
    def fail(*args):
        raise RuntimeError("do-not-log-this-secret")
    monkeypatch.setattr(graph, "narrate_with_llm", fail)
    result = graph.run_investigator(small_ctx, enabled, "P1", "order_swing")
    assert result["source"] == "fallback"
    assert result["route"] == "escalate"
    assert "do-not-log-this-secret" not in str(result)


def test_AGT4_tool_limit_counts_calls_within_one_response(monkeypatch, small_ctx, enabled):
    calls = [SimpleNamespace(id=str(i), function=SimpleNamespace(
        name="get_inventory", arguments='{"product_id":"P1"}',
    )) for i in range(9)]
    message = SimpleNamespace(tool_calls=calls, model_dump=lambda **kw: {})
    monkeypatch.setattr(llm_client, "complete", lambda *a, **kw: SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
    ))
    with pytest.raises(BudgetExceeded):
        llm_client.investigate_with_llm(small_ctx, enabled, "P1", "order_swing", None)
    assert small_ctx.llm_tool_calls == 8
    assert len(small_ctx.tool_outputs) == 8


def test_OBS5_zero_budget_prevents_network_request(enabled):
    import time
    with pytest.raises(BudgetExceeded, match="cost cap"):
        complete(enabled, RunBudget(0), time.monotonic() + 30, messages=[])


def test_AGT3_retry_keeps_evidence_and_submits_when_tool_budget_is_used(monkeypatch, small_ctx, enabled):
    finding = build_fallback_finding(small_ctx, "P1", "order_swing")
    small_ctx.llm_tool_calls = 8

    def respond(*args, **kwargs):
        assert kwargs["tool_choice"]["function"]["name"] == "submit_finding"
        assert len(kwargs["tools"]) == 1
        assert finding.recommended_calc_id in kwargs["messages"][1]["content"]
        message = SimpleNamespace(tool_calls=[SimpleNamespace(
            id="submit", function=SimpleNamespace(name="submit_finding", arguments=finding.model_dump_json()),
        )], model_dump=lambda **kw: {})
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    monkeypatch.setattr(llm_client, "complete", respond)
    actual, _ = llm_client.investigate_with_llm(small_ctx, enabled, "P1", "order_swing", "faithfulness")
    assert actual == finding
    assert small_ctx.llm_tool_calls == 8


def test_AGT6_fallback_has_three_faithful_reasons(small_ctx, enabled):
    result = graph.run_investigator(small_ctx, replace(enabled, llm_mode="off"), "P1", "order_swing")
    assert len(result["finding"].reasons) == 3
    assert result["route"] == "auto_approve"
    assert not result["guardrail_events"]


def test_GRD3_units_cannot_be_relabelled_as_cases(monkeypatch, small_ctx, enabled):
    def malicious(ctx, settings, pid, kind, hint):
        finding = build_fallback_finding(ctx, pid, kind)
        calc = ctx.calculations[finding.recommended_calc_id]
        finding.summary = f"Order {calc['order_units']} cases."
        finding.evidence.append(Evidence(
            value=calc["order_units"], unit="cases", source_tool="simulate_order",
            source_ref=calc["calc_id"],
        ))
        return finding, []

    monkeypatch.setattr(graph, "investigate_with_llm", malicious)
    result = graph.run_investigator(small_ctx, enabled, "P1", "order_swing")
    assert result["source"] == "fallback"
    assert result["route"] == "escalate"
    assert any("case quantity" in e["details"] for e in result["guardrail_events"])


def test_AGT3_evidence_does_not_leak_between_investigations(monkeypatch, small_ctx, enabled):
    call_tool(small_ctx, "simulate_order", {"product_id": "P1"})
    old = build_fallback_finding(small_ctx, "P1", "order_swing")
    monkeypatch.setattr(graph, "investigate_with_llm", lambda *args: (old, []))
    result = graph.run_investigator(small_ctx, enabled, "P1", "order_swing")
    assert any(e["type"] == "provenance" for e in result["guardrail_events"])
    assert result["retry_count"] == 2
    assert len(result["trace"]) < 10


def test_GRD3_duration_cannot_match_an_unrelated_tool_number(monkeypatch, small_ctx, enabled):
    def malicious(ctx, settings, pid, kind, hint):
        finding = build_fallback_finding(ctx, pid, kind)
        finding.summary = "No sales for six weeks."
        return finding, []

    monkeypatch.setattr(graph, "investigate_with_llm", malicious)
    result = graph.run_investigator(small_ctx, enabled, "P1", "order_swing")
    assert result["source"] == "fallback"
    assert any("duration" in e["details"] for e in result["guardrail_events"])


def test_GRD7_events_and_calculations_persist_idempotently(monkeypatch, small_ctx, enabled):
    import duckdb
    from freshpilot.agents.persistence import persist_evaluated_findings

    def fail(*args):
        raise RuntimeError("test")
    monkeypatch.setattr(graph, "investigate_with_llm", fail)
    result = graph.run_investigator(small_ctx, enabled, "P1", "order_swing")
    payload = {**result, "finding": result["finding"].model_dump()}
    with duckdb.connect(":memory:") as con:
        con.execute("CREATE TABLE products (product_id VARCHAR PRIMARY KEY)")
        con.execute("INSERT INTO products VALUES ('P1')")
        persist_evaluated_findings(con, "2025-06-30", [payload])
        count = con.execute("SELECT count(*) FROM guardrail_events").fetchone()[0]
        assert count > 0
        persist_evaluated_findings(con, "2025-06-30", [payload])
        assert con.execute("SELECT count(*) FROM guardrail_events").fetchone()[0] == count
        assert con.execute("SELECT count(*) FROM findings").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM calculations").fetchone()[0] == 1


@pytest.mark.parametrize("overrides", [{"on_order_qty": -1}, {"book_on_hand": float("nan")}, {"qty": 99}])
def test_ORD7_invalid_overrides_rejected(small_ctx, overrides):
    with pytest.raises(ValueError):
        call_tool(small_ctx, "simulate_order", {"product_id": "P1", "overrides": overrides})


def test_AGT4_request_deadline_cancels_slow_provider(monkeypatch, enabled):
    import asyncio
    import time

    from freshpilot.agents import budget as module

    cancelled = []
    async def create(**kwargs):
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.append(True)
    class Client:
        chat = SimpleNamespace(completions=SimpleNamespace(create=create))
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
    monkeypatch.setattr(module, "AsyncOpenAI", lambda **kwargs: Client())
    budget = RunBudget(1)
    with pytest.raises(TimeoutError):
        complete(enabled, budget, time.monotonic() + 0.02, messages=[])
    assert cancelled
    assert budget.cost_usd > 0  # Unknown usage stays conservatively reserved.


def test_OBS5_usage_is_accounted_across_calls(monkeypatch, enabled):
    import time

    from freshpilot.agents import budget as module

    async def create(**kwargs):
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20))
    class Client:
        chat = SimpleNamespace(completions=SimpleNamespace(create=create))
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
    monkeypatch.setattr(module, "AsyncOpenAI", lambda **kwargs: Client())
    budget = RunBudget(1)
    for _ in range(2):
        complete(enabled, budget, time.monotonic() + 30, messages=[])
    assert budget.tokens_in == 200
    assert budget.tokens_out == 40
    assert budget.cost_usd == pytest.approx((200 * 0.15 + 40 * 0.6) / 1_000_000)
