"""Detection Agent.

Responsibility: detect missing fields, stale records, expired documents,
policy mismatch, or changed documents on the customer's current record +
metadata snapshot (checked in that priority order, per spec).

Input: trigger_event, record, metadata (already loaded into state by the
dispatcher in app.py before the graph is invoked).
Output: gap_event (or None if the record is clean).
Downstream: Enrichment Agent (if a gap is found) or graph END (no gap).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any, Optional

from agents.audit import log_step
from models.state import RemediationState
from services import config, db

# Not exposed as a configurable threshold for this MVP — see README,
# Assumptions & Trade-offs. Only confidence_threshold and
# expiry_threshold_days are wired to the config table per the spec's
# explicit "Configurability" requirement.
STALE_THRESHOLD_DAYS = 180

PRIMARY_DOC_INDEX = 0  # this MVP's mock records carry a single primary document


def _parse_date(value: Optional[str]):
    if not value:
        return None
    return datetime.fromisoformat(value)


def _is_stale(last_checked: Optional[str]) -> bool:
    parsed = _parse_date(last_checked)
    if parsed is None:
        return True
    return (datetime.utcnow() - parsed) > timedelta(days=STALE_THRESHOLD_DAYS)


def _is_expired(document_expiry: Optional[str]) -> bool:
    if not document_expiry:
        return True
    expiry = date.fromisoformat(document_expiry)
    threshold = config.get_expiry_threshold_days()
    return expiry <= date.today() + timedelta(days=threshold)


def _build_gap_event(state: RemediationState, gap_type: str, reason: str, details: dict) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "customer_id": state["customer_id"],
        "gap_type": gap_type,
        "details": {"reason": reason, **details},
        "detected_at": datetime.utcnow().isoformat(),
        "trigger_source": (state.get("trigger_event") or {}).get("type", "MANUAL"),
        "status": "OPEN",
        "run_id": state["run_id"],
    }


def detection_node(state: RemediationState) -> RemediationState:
    # Resuming an already-detected case (human-review pause/resume, or a
    # replayed trigger with the same run_id) reuses the existing gap_event
    # instead of inserting a duplicate row — this is the node-level half of
    # the idempotency guarantee (the dispatcher-level half lives in graph/build.py).
    existing = db.find_gap_event_by_run_id(state["run_id"])
    if existing:
        state["gap_event"] = existing
        log_step(
            state, agent="detection", previous_state="NEW", new_state="GAP_DETECTED",
            evidence={"reason": existing["details"].get("reason"), "reused_existing": True},
        )
        return state

    record = state["record"]
    metadata = state["metadata"]
    trigger = state.get("trigger_event") or {}

    missing = [f for f in ("beneficial_owner", "tax_id") if not record.get(f)]
    if missing:
        gap = _build_gap_event(
            state, "MISSING_FIELD",
            f"Missing mandatory field(s): {', '.join(missing)}",
            {"missing_fields": missing},
        )
    elif _is_stale(metadata.get("last_checked")):
        gap = _build_gap_event(
            state, "STALE_RECORD",
            f"Record last checked on {metadata.get('last_checked')}, exceeds {STALE_THRESHOLD_DAYS}-day staleness window",
            {"last_checked": metadata.get("last_checked")},
        )
    elif _is_expired(metadata.get("document_expiry")):
        gap = _build_gap_event(
            state, "EXPIRED_DOC",
            f"Primary document expires/expired on {metadata.get('document_expiry')}",
            {"document_expiry": metadata.get("document_expiry")},
        )
    elif record.get("policy_version", 0) < config.get_current_policy_version():
        gap = _build_gap_event(
            state, "POLICY_MISMATCH",
            f"Record on policy v{record.get('policy_version')}, current is v{config.get_current_policy_version()}",
            {"record_policy_version": record.get("policy_version")},
        )
    elif trigger.get("type") == "DOCUMENT_UPLOADED" and trigger.get("new_hash") != metadata.get("document_hash"):
        gap = _build_gap_event(
            state, "CHANGED_DOC",
            "Newly uploaded document hash differs from stored hash",
            {"new_hash": trigger.get("new_hash"), "stored_hash": metadata.get("document_hash")},
        )
    else:
        gap = None

    if gap is None:
        state["gap_event"] = None
        state["status"] = "COMPLETED"
        log_step(
            state, agent="detection", previous_state="NEW", new_state="NO_GAP_EXIT",
            evidence={"reason": "No gap detected — record is current and complete"},
        )
        return state

    db.insert_gap_event(gap)
    state["gap_event"] = gap
    state["status"] = "RUNNING"
    log_step(
        state, agent="detection", previous_state="NEW", new_state="GAP_DETECTED",
        evidence={"gap_type": gap["gap_type"], "reason": gap["details"]["reason"]},
    )
    return state
