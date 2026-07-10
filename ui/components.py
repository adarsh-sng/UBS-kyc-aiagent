"""Streamlit presentation helpers: status badges, agent-step cards, and the
audit timeline. Kept separate from app.py so the page script stays readable.
"""
from __future__ import annotations

from typing import Any

import streamlit as st

from services import db

BADGE_COLORS = {"GREEN": "#28a745", "YELLOW": "#ffc107", "RED": "#dc3545"}

AGENT_TITLES = {
    "detection": "Detection Agent",
    "enrichment": "Enrichment Agent (internal sources)",
    "fetch_external_source": "Fetch External Source Agent",
    "verification": "Verification Agent",
    "manager": "Manager Agent",
    "communication": "Communication Agent",
    "rm_queue": "Human Review (RM Queue)",
    "audit": "Audit Agent",
}


def compute_status_badge(customer_id: str) -> tuple[str, str]:
    """Derives a (color, label) badge from the customer's full audit trail
    rather than a single stored status column — see README, Assumptions &
    Trade-offs, for why (a coarse status field can't distinguish 'routed to
    human review after a verification failure' from 'routed to human review
    directly', but the badge legend requires that distinction: RED vs YELLOW)."""
    trail = db.get_audit_trail(customer_id)
    if not trail:
        return "GREEN", "No Action Needed"

    last = trail[-1]
    if last["new_state"] == "COMPLETE":
        return "GREEN", "Completed"

    if any(a["new_state"].startswith("VERIFICATION_FAILED") for a in trail):
        return "RED", "Verification Failed"

    if last["new_state"] == "NO_GAP_EXIT":
        return "GREEN", "No Action Needed"

    return "YELLOW", "Human Review"


def render_badge(color: str, label: str) -> str:
    hex_color = BADGE_COLORS.get(color, "#6c757d")
    return (
        f'<span style="background-color:{hex_color}; color:white; padding:2px 10px; '
        f'border-radius:10px; font-size:0.85em; font-weight:600;">{label}</span>'
    )


def render_timeline(audit_trail: list[dict[str, Any]]) -> None:
    if not audit_trail:
        st.caption("No audit entries yet.")
        return
    for entry in audit_trail:
        ts = (entry.get("timestamp") or "")[:19].replace("T", " ")
        st.markdown(f"**{ts}** — `{entry['agent']}` → **{entry['new_state']}**")
        reason = (entry.get("evidence") or {}).get("reason")
        if reason:
            st.caption(reason)


def render_agent_card(node_name: str, snapshot: dict[str, Any]) -> None:
    with st.container(border=True):
        st.markdown(f"#### {AGENT_TITLES.get(node_name, node_name)}")

        if node_name == "detection":
            gap = snapshot.get("gap_event")
            if gap:
                st.write(f"Gap type: **{gap['gap_type']}**")
                st.caption(gap["details"].get("reason", ""))
            else:
                st.success("No gap detected — record is current and complete.")

        elif node_name == "enrichment":
            er = snapshot.get("enrichment_result") or {}
            st.write(f"Confidence so far: **{er.get('confidence', 0):.2f}**")
            if er.get("sources_used"):
                st.write(f"Internal sources used: {', '.join(er['sources_used'])}")
            if er.get("updated_fields"):
                st.json(er["updated_fields"])
            if not er.get("sources_used"):
                st.caption("Nothing resolved internally — handing off to Fetch External Source Agent.")

        elif node_name == "fetch_external_source":
            er = snapshot.get("enrichment_result") or {}
            d = snapshot.get("decision") or {}
            retry = snapshot.get("retry_count", 0)
            if retry:
                st.caption(f"Retry attempt {retry} — trying next-best external source.")
            st.write(f"Confidence: **{er.get('confidence', 0):.2f}**  |  Outcome: **{d.get('outcome')}**")
            if er.get("sources_used"):
                st.write(f"Sources used (internal + external): {', '.join(er['sources_used'])}")
            if er.get("sources_unavailable"):
                st.warning(f"Unavailable sources: {', '.join(er['sources_unavailable'])}")
            st.caption(d.get("reason", ""))

        elif node_name == "verification":
            vr = snapshot.get("verification_result") or {}
            retry = snapshot.get("retry_count", 0)
            if vr.get("verified"):
                st.success(vr.get("reason", ""))
            elif snapshot.get("status") == "RETRYING":
                st.warning(f"{vr.get('reason', '')} — retrying (attempt {retry}/2)")
            else:
                st.error(f"{vr.get('reason', '')} — retries exhausted, escalating to a manager")

        elif node_name == "manager":
            manager = snapshot.get("assigned_manager") or {}
            st.write(f"Assigned to **{manager.get('name', 'Unassigned')}** ({manager.get('specialty', '')})")
            summary = snapshot.get("manager_summary")
            if summary:
                st.caption(summary)

        elif node_name == "communication":
            draft = snapshot.get("client_message_draft") or ""
            st.text_area(
                "Drafted client message (for RM review/send)", value=draft, height=120, disabled=True,
                label_visibility="collapsed", key=f"draft_card_{snapshot.get('run_id')}",
            )

        elif node_name == "rm_queue":
            hr = snapshot.get("human_review") or {}
            if hr.get("status") == "PENDING":
                manager = hr.get("assigned_manager") or {}
                st.warning(f"Routed to {manager.get('name', 'Relationship Manager')}: {hr.get('reason', '')}")
            else:
                st.info(f"Resolved by RM: {hr.get('rm_notes', '')}")

        elif node_name == "audit":
            st.success("Record updated and case closed. See Audit Trail below.")
