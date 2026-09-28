"""GRD-1: reject any recommendation whose quantity lacks a valid calc_id."""

from __future__ import annotations

from freshpilot.agents.schemas import Finding

# Actions that recommend a specific quantity change and so must carry a calc_id.
ACTIONS_REQUIRING_CALC_ID = {"adjust"}


def check_provenance(finding: Finding, known_calc_ids: set[str]) -> bool:
    if finding.recommended_action in ACTIONS_REQUIRING_CALC_ID:
        return finding.recommended_calc_id is not None and finding.recommended_calc_id in known_calc_ids
    if finding.recommended_calc_id is not None:
        return finding.recommended_calc_id in known_calc_ids
    return True
