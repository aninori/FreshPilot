"""M6 acceptance test: routing accuracy >= 0.90.

Ground truth: planted `phantom_stock`/`shrink_spike` answer-key events are
genuine data-quality problems that must escalate; routine items with no
exception are demand-driven adjustments that should auto-approve. This is the
same distinction G6 (<=1% escalated) and EXC-6's categories are built around.
"""

from __future__ import annotations

import pandas as pd
from freshpilot.agents.graph import run_investigator
from freshpilot.agents.tools import ToolContext
from freshpilot.config import Settings

ROUTING_ACCURACY_TARGET = 0.90

FALLBACK_SETTINGS = Settings(
    seed=42, openai_api_key=None, openai_model="gpt-4o-mini", llm_mode="off",
    max_run_usd=1.0, demo_date=None, langfuse_public_key=None, langfuse_secret_key=None, langfuse_host=None,
)


def test_routing_accuracy_at_least_90pct(
    tool_context: ToolContext, generated_tables: dict[str, pd.DataFrame]
) -> None:
    answer_key = generated_tables["answer_key"]

    cases: list[tuple[str, str, bool]] = []  # (product_id, exception_type, should_escalate)

    phantom = answer_key[answer_key["type"] == "phantom_stock"]
    for row in phantom.itertuples(index=False):
        cases.append((row.product_id, "phantom_stock", True))

    shrink = answer_key[answer_key["type"] == "shrink_spike"]
    for row in shrink.itertuples(index=False):
        cases.append((row.product_id, "shrink_spike", True))

    planted_ids = {c[0] for c in cases}
    routine_sample = tool_context.products[~tool_context.products["product_id"].isin(planted_ids)][
        "product_id"
    ].sample(20, random_state=42)
    for product_id in routine_sample:
        cases.append((product_id, "order_swing", False))

    correct = 0
    for product_id, exception_type, should_escalate in cases:
        result = run_investigator(tool_context, FALLBACK_SETTINGS, product_id, exception_type)  # type: ignore[arg-type]
        escalated = result["route"] == "escalate"
        if escalated == should_escalate:
            correct += 1

    accuracy = correct / len(cases)
    assert accuracy >= ROUTING_ACCURACY_TARGET, f"accuracy={accuracy:.2f} ({correct}/{len(cases)})"
