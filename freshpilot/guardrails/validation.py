"""Validate agent claims against the trusted, per-investigation tool ledger."""

from __future__ import annotations

import math
import re
from typing import Any

from freshpilot.agents.schemas import Finding
from freshpilot.guardrails.faithfulness import extract_numbers, is_faithful, normalize_number_words
from freshpilot.guardrails.policy_band import check_policy_band
from freshpilot.guardrails.provenance import check_provenance


def numeric_values(value: Any) -> list[float]:
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [float(value)] if math.isfinite(value) else []
    if isinstance(value, dict):
        return [n for v in value.values() for n in numeric_values(v)]
    if isinstance(value, (list, tuple)):
        return [n for v in value for n in numeric_values(v)]
    return []


def validate_finding(
    finding: Finding, product_id: str, exception_type: str, outputs: list[dict[str, Any]],
    calculations: dict[str, dict[str, Any]], base: dict[str, Any], capacity: float | None,
) -> list[str]:
    errors: list[str] = []
    if finding.product_id != product_id or finding.exception_type != exception_type:
        errors.append("identity: finding does not match the investigated item")
    called_ids = {
        str(row["result"]["calc_id"]) for row in outputs
        if row["name"] == "simulate_order" and row["result"].get("product_id") == product_id
    }
    if not check_provenance(finding, called_ids):
        errors.append("provenance: calculation was not returned by this investigation")
    known = numeric_values([row["result"] for row in outputs])
    for evidence in finding.evidence:
        sources = [row for row in outputs if row["name"] == evidence.source_tool
                   and row["source_ref"] == evidence.source_ref]
        if not sources or not is_faithful(str(evidence.value), numeric_values(sources[0]["result"]))[0]:
            errors.append("evidence: value or source does not match a recorded tool result")
        elif evidence.source_tool == "simulate_order" and evidence.unit.lower().strip() in {"case", "cases"}:
            source = sources[0]["result"]
            if source.get("quantity_unit") != "cases" or not is_faithful(
                str(evidence.value), [float(source["order_cases"])],
            )[0]:
                errors.append("evidence: case quantity does not match the recorded order cases")
    text = normalize_number_words(" ".join([finding.summary, *finding.reasons, finding.precondition or ""]))
    if finding.recommended_action == "shelf_check_then_order" and not finding.precondition:
        errors.append("precondition: shelf check must be required before ordering")
    if finding.recommended_action in {"approve", "adjust", "shelf_check_then_order"}:
        if extract_numbers(text) and not finding.recommended_calc_id:
            errors.append("provenance: numeric order recommendation requires a calculation")
    if not is_faithful(text, known)[0]:
        errors.append("faithfulness: narrative contains unsupported numbers")
    if finding.estimated_impact_usd is not None:
        impacts = [c["breakdown"]["estimated_impact_usd"] for c in calculations.values()
                   if "estimated_impact_usd" in c["breakdown"]]
        if not is_faithful(str(finding.estimated_impact_usd), impacts)[0]:
            errors.append("faithfulness: impact lacks a recorded calculation")
    if len(finding.summary.split()) > 60 or len(finding.reasons) != 3:
        errors.append("narration: require at most sixty words and exactly three reasons")
    if finding.recommended_calc_id in called_ids:
        calc = calculations[finding.recommended_calc_id]
        for value, unit in re.findall(r"\b(\d+(?:\.\d+)?)\s+(days?|weeks?)\b", text, re.IGNORECASE):
            days = float(value) * (7 if unit.lower().startswith("week") else 1)
            recorded_days = calc["breakdown"].get("consecutive_zero_sales_days")
            if recorded_days is None or not is_faithful(str(days), [float(recorded_days)])[0]:
                errors.append("faithfulness: duration is not a recorded zero-sales duration")
        case_claims = re.findall(r"\b(\d+(?:\.\d+)?)\s+cases?\b", text, flags=re.IGNORECASE)
        if case_claims and (calc.get("quantity_unit") != "cases" or any(
            not is_faithful(value, [float(calc["order_cases"])])[0] for value in case_claims
        )):
            errors.append("faithfulness: narrative case quantity differs from the recommended calculation")
        if not check_policy_band(float(calc["order_units"]), float(base["order_units"]), capacity).within_band:
            errors.append("policy_band: recommendation exceeds the order band or shelf capacity")
    return errors
