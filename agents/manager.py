"""Manager Agent.

Chooses the right Relationship Manager for this case (by gap-type
specialty — see mock_data/managers.py) and produces a concise case
summary for them to review, via the pluggable LLM client (falls back to
a deterministic template when no API key is configured — see
services/llm_client.py).

Input: gap_event, enrichment_result, decision and/or verification_result
(whichever routed here).
Output: assigned_manager, manager_summary.
Downstream: Communication Agent.
"""
from __future__ import annotations

from agents.audit import log_step
from models.state import RemediationState
from mock_data.managers import assign_manager
from services import llm_client


def _case_reason(state: RemediationState) -> str:
    verification = state.get("verification_result")
    if verification and not verification["verified"]:
        return verification["reason"]
    return state["decision"]["reason"]


def _template_summary(state: RemediationState, reason: str) -> str:
    gap = state["gap_event"]
    record = state["record"]
    enrichment = state.get("enrichment_result") or {}
    return (
        f"KYC remediation case for {record['company_name']} ({state['customer_id']}).\n"
        f"Gap type: {gap['gap_type']}. Reason for escalation: {reason}.\n"
        f"Enrichment confidence: {enrichment.get('confidence', 0):.2f}. "
        f"Sources tried: {', '.join(enrichment.get('sources_used', [])) or 'none'}. "
        f"Please review and follow up with the customer as needed."
    )


def manager_node(state: RemediationState) -> RemediationState:
    gap = state["gap_event"]
    reason = _case_reason(state)
    manager = assign_manager(gap["gap_type"], gap["details"].get("missing_fields"))
    template = _template_summary(state, reason)
    summary = llm_client.complete(
        system=(
            "You are an internal compliance assistant summarizing a corporate KYC "
            "remediation case for a Relationship Manager in 3-4 concise sentences."
        ),
        prompt=template,
        template_fallback=template,
    )

    state["assigned_manager"] = manager
    state["manager_summary"] = summary
    previous = "ESCALATING" if state.get("status") == "ESCALATING" else f"DECISION_{state['decision']['outcome']}"
    log_step(
        state, agent="manager", previous_state=previous, new_state="MANAGER_ASSIGNED",
        evidence={"manager": manager["name"], "specialty": manager["specialty"], "summary": summary},
    )
    return state
