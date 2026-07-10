"""Streamlit dashboard for the End-to-End Corporate KYC Remediation Loop.

Run with:  streamlit run app.py

Layout: sidebar (config + safety controls + reset) -> customer table ->
trigger panel (the 3 event types) -> Run Remediation + live agent cards ->
Relationship Manager (human review) panel -> Audit Trail.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import streamlit as st

from events.bus import EventBus
from events.types import DocumentUploaded, ExpiryReminder, ManualReviewCompleted, PolicyUpdated
from agents import human_review
from graph import build as graph_build
from migration import seed
from services import config, db
from ui import components

st.set_page_config(page_title="Corporate KYC Remediation Loop", layout="wide")


# --------------------------------------------------------------- bootstrap --
def _ensure_seeded() -> None:
    db.init_db()
    config.seed_defaults()
    if not db.list_records_with_metadata():
        seed.seed()


_ensure_seeded()


# ------------------------------------------------------------ event handlers --
def on_policy_updated(event: PolicyUpdated) -> dict:
    config.set_current_policy_version(event.new_policy_version)
    affected = [r for r in db.list_records_with_metadata() if r["policy_version"] < event.new_policy_version]
    fanned_out = []
    for r in affected:
        run_id = graph_build.make_run_id(r["id"], "POLICY_UPDATE", str(event.new_policy_version))
        if graph_build.is_duplicate_trigger(run_id):
            continue
        trigger_event = {"type": "POLICY_UPDATE", "new_version": event.new_policy_version}
        nodes = list(graph_build.run_remediation_stream(r["id"], trigger_event, run_id))
        fanned_out.append((r["id"], nodes))
    return {"fanned_out": fanned_out}


def on_document_uploaded(event: DocumentUploaded) -> dict:
    run_id = graph_build.make_run_id(event.customer_id, "DOCUMENT_UPLOADED", event.new_hash)
    if graph_build.is_duplicate_trigger(run_id):
        return {"duplicate": True, "nodes": []}
    trigger_event = {"type": "DOCUMENT_UPLOADED", "new_hash": event.new_hash}
    nodes = list(graph_build.run_remediation_stream(event.customer_id, trigger_event, run_id))
    return {"duplicate": False, "nodes": nodes}


def on_expiry_reminder(event: ExpiryReminder) -> dict:
    threshold = config.get_expiry_threshold_days()
    cutoff = event.check_date + timedelta(days=threshold)
    candidates = [
        m for m in db.list_metadata()
        if m.get("document_expiry") and date.fromisoformat(m["document_expiry"]) <= cutoff
    ]
    fanned_out = []
    for m in candidates:
        run_id = graph_build.make_run_id(m["customer_id"], "EXPIRY_CHECK", m["document_expiry"])
        if graph_build.is_duplicate_trigger(run_id):
            continue
        trigger_event = {"type": "EXPIRY_CHECK", "document_expiry": m["document_expiry"]}
        nodes = list(graph_build.run_remediation_stream(m["customer_id"], trigger_event, run_id))
        fanned_out.append((m["customer_id"], nodes))
    return {"fanned_out": fanned_out}


def on_manual_review_completed(event: ManualReviewCompleted) -> None:
    # Purely observational — the actual graph resume is driven directly by
    # the RM panel button (see below). Published for architectural/
    # observability completeness (all 4 event types flow through the bus).
    print(f"[event] ManualReviewCompleted customer={event.customer_id} run_id={event.run_id}")


if "event_bus" not in st.session_state:
    bus = EventBus()
    bus.subscribe(PolicyUpdated, on_policy_updated)
    bus.subscribe(DocumentUploaded, on_document_uploaded)
    bus.subscribe(ExpiryReminder, on_expiry_reminder)
    bus.subscribe(ManualReviewCompleted, on_manual_review_completed)
    st.session_state["event_bus"] = bus
bus: EventBus = st.session_state["event_bus"]

st.session_state.setdefault("last_run_nodes", [])
st.session_state.setdefault("last_run_customer", None)
st.session_state.setdefault("pending_state", None)


def _record_run(customer_id: str, nodes: list) -> None:
    st.session_state["last_run_nodes"] = nodes
    st.session_state["last_run_customer"] = customer_id
    final_state = nodes[-1][1] if nodes else None
    st.session_state["pending_state"] = final_state if final_state and final_state.get("status") == "HUMAN_REVIEW" else None


# --------------------------------------------------------------------- sidebar --
st.sidebar.title("Corporate KYC Remediation Loop")
st.sidebar.caption("Continuous detection -> enrichment -> decision -> verification -> audit")

st.sidebar.header("Configuration")
conf = st.sidebar.slider("Confidence threshold (AUTO cutoff)", 0.50, 1.00, config.get_confidence_threshold(), 0.01)
if conf != config.get_confidence_threshold():
    config.set_confidence_threshold(conf)

expiry_days = st.sidebar.number_input(
    "Expiry threshold (days)", min_value=0, value=config.get_expiry_threshold_days(), step=5
)
if int(expiry_days) != config.get_expiry_threshold_days():
    config.set_expiry_threshold_days(int(expiry_days))

paused = st.sidebar.checkbox("Pause Automation (safety control)", value=config.get_automation_paused())
if paused != config.get_automation_paused():
    config.set_automation_paused(paused)
if paused:
    st.sidebar.warning("Automation paused — every case will route to Human Review.")

st.sidebar.header("Demo Controls")
if st.sidebar.button("Reset Demo Data"):
    seed.seed(reset=True)
    st.session_state["last_run_nodes"] = []
    st.session_state["last_run_customer"] = None
    st.session_state["pending_state"] = None
    st.rerun()


# ---------------------------------------------------------------- customer table --
st.title("Corporate KYC Book")

records = db.list_records_with_metadata()
badge_emoji = {"GREEN": "\U0001F7E2", "YELLOW": "\U0001F7E1", "RED": "\U0001F534"}
table_rows = []
for r in records:
    color, label = components.compute_status_badge(r["id"])
    table_rows.append({
        "Company": r["company_name"],
        "Policy v": r["policy_version"],
        "Beneficial Owner": r.get("beneficial_owner") or "—",
        "Tax ID": r.get("tax_id") or "—",
        "Doc Expiry": r.get("document_expiry") or "—",
        "Status": f"{badge_emoji[color]} {label}",
    })
st.dataframe(table_rows, width="stretch", hide_index=True)

customer_options = {r["company_name"]: r["id"] for r in records}
selected_name = st.selectbox("Select a customer to inspect / remediate", list(customer_options.keys()))
selected_customer_id = customer_options[selected_name]

badge_color, badge_label = components.compute_status_badge(selected_customer_id)
st.markdown(f"### {selected_name} &nbsp; {components.render_badge(badge_color, badge_label)}", unsafe_allow_html=True)


# ---------------------------------------------------------------- trigger panel --
st.header("Triggers")
t1, t2, t3 = st.columns(3)

with t1:
    st.markdown("**1. Policy Update**")
    new_version = st.number_input(
        "New policy version", min_value=config.get_current_policy_version() + 1,
        value=config.get_current_policy_version() + 1, step=1, key="policy_version_input",
    )
    if st.button("Publish PolicyUpdated"):
        result = bus.publish(PolicyUpdated(new_policy_version=int(new_version)))[0]
        st.success(f"Policy bumped to v{new_version}. {len(result['fanned_out'])} case(s) remediated.")
        st.rerun()

with t2:
    st.markdown("**2. New Document Uploaded**")
    hash_key = f"doc_hash_{selected_customer_id}"
    if hash_key not in st.session_state:
        st.session_state[hash_key] = f"hash-{uuid.uuid4().hex[:10]}"
    st.caption(f"Simulated upload hash: `{st.session_state[hash_key]}`")
    doc_col1, doc_col2 = st.columns(2)
    with doc_col1:
        if st.button("Simulate Upload"):
            result = bus.publish(DocumentUploaded(
                customer_id=selected_customer_id, document_type="incorporation_certificate",
                new_hash=st.session_state[hash_key],
            ))[0]
            if result["duplicate"]:
                st.warning("Duplicate trigger ignored — an identical case is already open/completed for this document hash.")
            else:
                _record_run(selected_customer_id, result["nodes"])
            st.rerun()
    with doc_col2:
        if st.button("New Hash"):
            st.session_state[hash_key] = f"hash-{uuid.uuid4().hex[:10]}"
            st.rerun()

with t3:
    st.markdown("**3. Expiry Check (periodic)**")
    st.caption(f"Flags documents expiring within {config.get_expiry_threshold_days()} days.")
    if st.button("Run Daily Expiry Check"):
        result = bus.publish(ExpiryReminder())[0]
        st.success(f"Expiry scan complete — {len(result['fanned_out'])} case(s) triggered.")
        st.rerun()


# ---------------------------------------------------------------- run remediation --
st.header("Run Remediation")
if st.button("Run Remediation for selected customer", type="primary"):
    run_id = f"manual-{uuid.uuid4().hex[:12]}"
    nodes = list(graph_build.run_remediation_stream(selected_customer_id, {"type": "MANUAL"}, run_id))
    _record_run(selected_customer_id, nodes)
    st.rerun()

if st.session_state["last_run_customer"] == selected_customer_id and st.session_state["last_run_nodes"]:
    for node_name, snapshot in st.session_state["last_run_nodes"]:
        components.render_agent_card(node_name, snapshot)


# ---------------------------------------------------------------- human review --
pending = st.session_state.get("pending_state")
if pending and pending["customer_id"] == selected_customer_id:
    st.header("Relationship Manager Queue")
    manager = pending["human_review"].get("assigned_manager") or {}
    if manager:
        st.write(f"Assigned to **{manager.get('name')}** — {manager.get('specialty')}")
    st.warning(pending["human_review"]["reason"])
    draft = pending["human_review"].get("client_message_draft")
    if draft:
        st.text_area(
            "Drafted client message (for RM review/send)", value=draft, height=120, disabled=True,
            key=f"draft_panel_{pending.get('run_id')}",
        )
    rm_notes = st.text_area(
        "RM notes (simulating the customer's follow-up response)",
        value="Customer confirmed the requested information by phone.",
        key="rm_notes_input",
    )
    if st.button("Simulate customer response received -> Resume"):
        bus.publish(ManualReviewCompleted(
            customer_id=selected_customer_id, run_id=pending["run_id"], rm_notes=rm_notes,
        ))
        resolved_state = human_review.resolve_human_review(pending, rm_notes)
        resume_nodes = list(graph_build.resume_remediation_stream(resolved_state))
        combined = st.session_state["last_run_nodes"] + resume_nodes
        _record_run(selected_customer_id, combined)
        st.rerun()


# ---------------------------------------------------------------- audit trail --
st.header("Audit Trail")
with st.expander(f"Replayable audit log for {selected_name}", expanded=False):
    trail = db.replay_audit(selected_customer_id)
    components.render_timeline(trail)
