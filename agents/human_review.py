"""Human Review — the Relationship Manager (RM) queue.

The AI never contacts the customer directly. Reached after the Manager
Agent has assigned an RM and the Communication Agent has drafted the
follow-up message (agents/manager.py, agents/communication.py). The case
waits here until the UI "resolves" it (standing in for: RM sends the
drafted message, customer responds, RM confirms). See graph/build.py for
how the pause/resume is wired.

Input: manager_summary, client_message_draft + rm_notes (supplied at
resolution time).
Output: human_review = {status, rm_notes, resolved_at, assigned_manager,
client_message_draft}.
Downstream: Verification Agent (re-run after resolution) or graph END
(while still pending).
"""
from __future__ import annotations

from datetime import datetime

from agents.audit import log_step
from models.state import RemediationState
from services import mock_sources, validation


def human_review_node(state: RemediationState) -> RemediationState:
    existing = state.get("human_review")
    if existing and existing.get("status") == "RESOLVED":
        # Resume path: already resolved by the UI before this re-invoke.
        log_step(
            state, agent="human_review", previous_state="HUMAN_REVIEW_PENDING",
            new_state="HUMAN_REVIEW_RESOLVED", evidence={"rm_notes": existing.get("rm_notes")},
        )
        state["status"] = "RUNNING"
        return state

    reason = state.get("manager_summary") or (state.get("verification_result") or {}).get("reason") or state["decision"]["reason"]
    state["human_review"] = {
        "status": "PENDING", "rm_notes": None, "resolved_at": None, "reason": reason,
        "assigned_manager": state.get("assigned_manager"),
        "client_message_draft": state.get("client_message_draft"),
    }
    state["status"] = "HUMAN_REVIEW"
    log_step(
        state, agent="human_review", previous_state="CLIENT_MESSAGE_DRAFTED",
        new_state="HUMAN_REVIEW_PENDING", evidence={"reason": reason},
    )
    return state


def resolve_human_review(state: RemediationState, rm_notes: str) -> RemediationState:
    """Called by the UI's 'Simulate customer response received' action.

    Stands in for: RM contacts the customer, customer supplies the missing
    information, RM confirms it. Mutates the cached state so the next graph
    invoke fast-forwards to re-verification.

    Re-validates rm_notes independently of the UI layer (app.py already
    validates before calling this) — defense-in-depth so this function is
    safe to call from any future caller, not just the Streamlit button.
    """
    rm_notes = validation.validate_rm_notes(rm_notes)
    gap = state["gap_event"]
    enrichment = state.get("enrichment_result") or {
        "updated_fields": {}, "confidence": 0.0, "evidence": {},
        "sources_used": [], "sources_unavailable": [], "internally_resolvable": False,
    }
    updated_fields = dict(enrichment.get("updated_fields", {}))

    verification = state.get("verification_result")
    if verification and not verification["verified"]:
        # Verification failure: RM/customer clarified the mismatched
        # field(s) directly, so the corrected value now matches the
        # official source (simulated — we look up the same ground truth
        # the Verification Agent checks against).
        official = mock_sources.OFFICIAL_VERIFICATION_SOURCE.get(state["customer_id"], {})
        for field in verification["evidence"]["mismatches"]:
            if field in official:
                updated_fields[field] = official[field]
    elif gap["gap_type"] == "MISSING_FIELD":
        for field in gap["details"].get("missing_fields", []):
            if field not in updated_fields:
                label = field.replace("_", " ").title()
                updated_fields[field] = f"{label} provided by customer via RM outreach"
    # STALE_RECORD / other gap types: no field-level change needed —
    # RM re-attestation alone is sufficient to close the loop.

    enrichment["updated_fields"] = updated_fields
    enrichment["confidence"] = 0.99
    enrichment["internally_resolvable"] = True
    # A source being 'down' earlier no longer matters once the RM/customer
    # has confirmed the data directly — clear it so fetch_external_source's
    # decision doesn't re-escalate on stale unavailability info.
    enrichment["sources_unavailable"] = []
    enrichment.setdefault("evidence", {})["human_review"] = {
        "source": "relationship_manager", "rm_notes": rm_notes,
    }
    state["enrichment_result"] = enrichment
    state["human_review"] = {
        **(state.get("human_review") or {}),
        "status": "RESOLVED", "rm_notes": rm_notes,
        "resolved_at": datetime.utcnow().isoformat(),
    }
    state["retry_count"] = 0
    return state
