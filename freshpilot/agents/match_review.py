"""CAT-6: the match review agent, for listings in CAT-5's 0.60-0.90 review band.

Per §5 principle 1 (LLMs plan and narrate; deterministic code decides), the
merge/keep_separate decision itself stays threshold-based — the LLM's role,
when enabled, is limited to phrasing the reason, the same narrate-only pattern
the investigator's narrate node uses.
"""

from __future__ import annotations

import time
from typing import Literal

from freshpilot.agents.budget import RunBudget, complete
from freshpilot.agents.schemas import Evidence, MatchReview
from freshpilot.config import Settings
from freshpilot.guardrails.faithfulness import is_faithful
from freshpilot.guardrails.unit_veto import veto_merge_if_unit_incompatible

MERGE_THRESHOLD = 0.75  # within the 0.60-0.90 review band (CAT-5): above this, lean merge


def build_fallback_match_review(
    pair_id: str, listing_raw_unit: str, listing_raw_pack: str, product_name: str, score: float,
    product_unit: str | None = None,
) -> MatchReview:
    evidence = [Evidence(value=score, unit="probability", source_tool="catalog_matcher", source_ref=pair_id)]

    if veto_merge_if_unit_incompatible(listing_raw_unit, listing_raw_pack, product_unit):
        return MatchReview(
            pair_id=pair_id,
            decision="keep_separate",
            reason="The listing's stated unit doesn't match its own pack description (GRD-4 unit veto).",
            evidence=evidence,
        )

    decision: Literal["merge", "keep_separate"] = "merge" if score >= MERGE_THRESHOLD else "keep_separate"
    reason = (
        f"Name and price line up closely enough with {product_name!r} to merge."
        if decision == "merge"
        else f"Not confident enough this listing is the same product as {product_name!r}."
    )
    return MatchReview(pair_id=pair_id, decision=decision, reason=reason, evidence=evidence)


def narrate_match_review(settings: Settings, review: MatchReview, budget: RunBudget | None = None) -> str:
    """LLM-enhanced reason phrasing only — the decision itself is untouched."""
    if not settings.llm_enabled:
        return review.reason
    budget = budget if budget is not None else RunBudget(settings.max_run_usd)
    user_msg = (
        "Rewrite this catalog-match reason for a store manager: plain language, one "
        f"sentence, using only the numbers already present: {review.model_dump_json()}"
    )
    try:
        response = complete(
            settings, budget, time.monotonic() + 30,
            messages=[
                {"role": "system", "content": "You phrase catalog-match reasons in plain language, one sentence."},
                {"role": "user", "content": user_msg},
            ],
        )
        polished = response.choices[0].message.content
    except Exception:  # noqa: BLE001 -- any LLM/network failure falls back to the template reason
        return review.reason
    if not polished:
        return review.reason
    known_values = [e.value for e in review.evidence]
    faithful, _ = is_faithful(polished, known_values)
    return polished if faithful else review.reason
