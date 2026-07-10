"""Audit Agent.

Two responsibilities, both described in the plan's Architecture section:
  1. `log_step` — a shared helper imported by every other agent so that
     EVERY action (not just the terminal one) is logged with timestamp,
     agent, decision, evidence, confidence, previous_state, new_state.
     Dedupes on (run_id, agent, new_state) via services.db.log_audit, which
     is what makes replayed/duplicate triggers idempotent.
  2. `audit_node` — the terminal LangGraph node reached only after a
     successful verification. It performs the "Update KYC" write-back
     (persisting enrichment results onto the corporate_records/metadata
     tables) and writes the closing COMPLETE audit row.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from models.state import RemediationState
from services import config, db


def log_step(
    state: RemediationState,
    agent: str,
    previous_state: str,
    new_state: str,
    evidence: Optional[dict[str, Any]] = None,
    decision: Optional[str] = None,
    confidence: Optional[float] = None,
) -> str:
    audit_id = str(uuid.uuid4())
    audit = {
        "id": audit_id,
        "customer_id": state["customer_id"],
        "timestamp": datetime.utcnow().isoformat(),
        "agent": agent,
        "decision": decision,
        "evidence": evidence or {},
        "confidence": confidence,
        "previous_state": previous_state,
        "new_state": new_state,
        "run_id": state["run_id"],
    }
    written = db.log_audit(audit)
    state.setdefault("audit_trail", []).append({**audit, "written": written})
    return audit_id


def audit_node(state: RemediationState) -> RemediationState:
    record = dict(state["record"])
    metadata = dict(state["metadata"])
    enrichment = state.get("enrichment_result") or {}
    updated_fields = enrichment.get("updated_fields", {})

    for field in ("beneficial_owner", "tax_id"):
        if field in updated_fields:
            record[field] = updated_fields[field]

    documents = list(record.get("documents") or [])
    if documents:
        primary = dict(documents[0])
        if "document_hash" in updated_fields:
            primary["hash"] = updated_fields["document_hash"]
        if "document_expiry" in updated_fields:
            primary["expiry_date"] = updated_fields["document_expiry"]
        documents[0] = primary
        record["documents"] = documents

    record["policy_version"] = updated_fields.get(
        "policy_version", config.get_current_policy_version()
    )
    record["status"] = "ACTIVE"
    db.upsert_record(record)

    now = datetime.utcnow().isoformat()
    metadata["last_checked"] = now
    metadata["policy_version"] = record["policy_version"]
    metadata["verification_status"] = "VERIFIED"
    metadata["last_remediation"] = now
    if documents:
        metadata["document_hash"] = documents[0].get("hash")
        metadata["document_expiry"] = documents[0].get("expiry_date")
    db.upsert_metadata(metadata)

    if state.get("gap_event"):
        db.set_gap_event_status(state["gap_event"]["id"], "RESOLVED")

    evidence = {
        "updated_record": {k: record.get(k) for k in ("beneficial_owner", "tax_id", "policy_version")}
    }
    if state.get("assigned_manager"):
        evidence["resolved_via_manager"] = state["assigned_manager"]["name"]

    audit_id = log_step(
        state, agent="audit", previous_state="VERIFIED", new_state="COMPLETE", evidence=evidence,
    )
    metadata["audit_pointer"] = audit_id
    db.upsert_metadata(metadata)

    state["record"] = record
    state["metadata"] = metadata
    state["status"] = "COMPLETED"
    return state
