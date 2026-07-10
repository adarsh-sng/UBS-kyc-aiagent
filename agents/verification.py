"""Verification Agent.

Compares enriched data against a mocked 'official source'. On mismatch,
retries up to MAX_RETRIES times — each retry routes back to the Fetch
External Source Agent to try the next-best source before re-verifying —
before escalating to the Manager Agent. Only beneficial_owner/tax_id are
checked (the other enrichable fields have no external ground truth in
this MVP).

Input: enrichment_result.
Output: verification_result = {verified, evidence, reason}; bumps
retry_count and sets state.status to RETRYING/ESCALATING to drive
agents/routing.py::route_after_verification.
Downstream: Audit Agent (verified) / Fetch External Source Agent (retry) /
Manager Agent (escalate, retries exhausted).
"""
from __future__ import annotations

from agents.audit import log_step
from models.state import RemediationState
from services import mock_sources

CHECKED_FIELDS = ("beneficial_owner", "tax_id")
MAX_RETRIES = 2


def verification_node(state: RemediationState) -> RemediationState:
    enrichment = state["enrichment_result"]
    customer_id = state["customer_id"]

    mismatches = {}
    for field in CHECKED_FIELDS:
        if field not in enrichment["updated_fields"]:
            continue
        value = enrichment["updated_fields"][field]
        if not mock_sources.verify_against_official_source(customer_id, field, value):
            mismatches[field] = value

    verified = len(mismatches) == 0
    reason = (
        "Enriched data matches official source (or no external ground truth to contest)"
        if verified
        else f"Mismatch vs official source on: {', '.join(mismatches)}"
    )
    result = {"verified": verified, "evidence": {"mismatches": mismatches}, "reason": reason}
    state["verification_result"] = result

    attempt = state.get("retry_count", 0)
    decision_label = f"DECISION_{state['decision']['outcome']}" + (f"_RETRY{attempt}" if attempt else "")

    if verified:
        state["status"] = "RUNNING"
        log_step(state, agent="verification", previous_state=decision_label, new_state="VERIFIED", evidence=result)
    elif attempt < MAX_RETRIES:
        state["retry_count"] = attempt + 1
        state["status"] = "RETRYING"
        log_step(
            state, agent="verification", previous_state=decision_label,
            new_state=f"VERIFICATION_FAILED_RETRY{attempt + 1}", evidence=result,
        )
    else:
        state["status"] = "ESCALATING"
        log_step(
            state, agent="verification", previous_state=decision_label,
            new_state="VERIFICATION_FAILED_ESCALATED", evidence=result,
        )
    return state
