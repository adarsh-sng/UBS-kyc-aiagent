"""Communication Agent.

Drafts the client-facing message summarizing what KYC information is
still needed — for the assigned Relationship Manager to review and send.
The AI never contacts the customer directly (see agents/human_review.py);
this agent only prepares the draft.

Input: gap_event, manager_summary.
Output: client_message_draft.
Downstream: Human Review (RM queue) — the RM reviews/sends this draft,
then the case waits for the customer's response.
"""
from __future__ import annotations

from agents.audit import log_step
from models.state import RemediationState
from services import llm_client

_FIELD_ASK = {
    "EXPIRED_DOC": "an updated, unexpired copy of your incorporation certificate",
    "STALE_RECORD": "confirmation that your registered company details are still current",
    "POLICY_MISMATCH": "confirmation of your details under our current KYC policy",
    "CHANGED_DOC": "confirmation of the recently uploaded document",
}


def _template_message(state: RemediationState) -> str:
    gap = state["gap_event"]
    record = state["record"]
    missing = gap["details"].get("missing_fields")
    if missing:
        ask = f"the following: {', '.join(f.replace('_', ' ') for f in missing)}"
    else:
        ask = _FIELD_ASK.get(gap["gap_type"], "additional information to complete your KYC review")
    return (
        f"Dear {record['company_name']} team,\n\n"
        f"As part of our periodic KYC review, we need to confirm {ask}. "
        f"Could you please provide this at your earliest convenience so we can complete your review?\n\n"
        f"Thank you,\nKYC Operations"
    )


def communication_node(state: RemediationState) -> RemediationState:
    template = _template_message(state)
    draft = llm_client.complete(
        system=(
            "You draft short, polite corporate KYC follow-up emails to business "
            "clients explaining exactly what information is outstanding."
        ),
        prompt=template,
        template_fallback=template,
        record=state["record"],
    )
    state["client_message_draft"] = draft
    log_step(
        state, agent="communication", previous_state="MANAGER_ASSIGNED", new_state="CLIENT_MESSAGE_DRAFTED",
        evidence={"draft_preview": draft[:200]},
    )
    return state
