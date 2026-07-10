"""Fetch External Source Agent.

Tries mocked EXTERNAL sources (Corporate Registry, Tax Database,
Beneficial Ownership DB) for whatever the internal-only Enrichment Agent
could not resolve, then makes the deterministic AUTO / VERIFY / HUMAN
call itself — this agent decides whether there's now enough information
to proceed automatically or whether the case needs a manager.

Input: gap_event, enrichment_result (from the internal pass).
Output: mutates enrichment_result with any newly-resolved external
fields; produces `decision` = {outcome, confidence, reason}.
Downstream: Verification Agent (AUTO/VERIFY) or Manager Agent (HUMAN).

Also re-entered on a bounded verification retry (see agents/verification.py)
— each pass skips sources already tried, so a retry means "try the next
best source," not "repeat the same failed lookup."
"""
from __future__ import annotations

import uuid
from datetime import datetime

from agents.audit import log_step
from models.state import RemediationState
from services import config, db, mock_sources

EXTERNAL_FIELD_SOURCES = {
    "beneficial_owner": ["beneficial_ownership_db", "corporate_registry"],
    "tax_id": ["tax_database"],
}


def _fetch_missing_fields(customer_id: str, missing_fields: list[str], result: dict) -> None:
    for field in missing_fields:
        if field in result["updated_fields"]:
            continue  # already resolved internally (or on a prior retry pass)
        for source_name in EXTERNAL_FIELD_SOURCES.get(field, []):
            if source_name in result["sources_used"] or source_name in result["sources_unavailable"]:
                continue  # already tried this source on a previous pass
            record = mock_sources.lookup(source_name, customer_id)
            if record is None:
                result["sources_unavailable"].append(source_name)
                continue
            if field in record:
                result["updated_fields"][field] = record[field]
                result["evidence"][field] = {
                    "source": source_name, "value": record[field], "confidence": record.get("confidence", 0.9),
                }
                result["sources_used"].append(source_name)
                result["confidence"] = max(result["confidence"], record.get("confidence", 0.9))
                break
    result["internally_resolvable"] = all(f in result["updated_fields"] for f in missing_fields)


def _fetch_expired_doc(customer_id: str, result: dict) -> None:
    if result.get("internally_resolvable"):
        return
    if "corporate_registry" in result["sources_used"] or "corporate_registry" in result["sources_unavailable"]:
        return
    record = mock_sources.lookup("corporate_registry", customer_id)
    if record is None:
        result["sources_unavailable"].append("corporate_registry")
        return
    if "incorporation_cert_expiry" in record:
        result["updated_fields"]["document_expiry"] = record["incorporation_cert_expiry"]
        result["evidence"]["document_expiry"] = {
            "source": "corporate_registry", "value": record["incorporation_cert_expiry"],
            "confidence": record.get("confidence", 0.9),
        }
        result["sources_used"].append("corporate_registry")
        result["confidence"] = max(result["confidence"], record.get("confidence", 0.9))
        result["internally_resolvable"] = True


def _fetch_policy_mismatch(result: dict) -> None:
    if result.get("internally_resolvable"):
        return
    new_version = config.get_current_policy_version()
    result["updated_fields"]["policy_version"] = new_version
    result["evidence"]["policy_version"] = {
        "source": "policy_engine", "value": "no conflicting external records under new policy", "confidence": 0.85,
    }
    result["confidence"] = max(result["confidence"], 0.85)
    result["internally_resolvable"] = True


def _make_decision(state: RemediationState, result: dict) -> dict:
    threshold = config.get_confidence_threshold()
    confidence = result["confidence"]

    if config.get_automation_paused():
        outcome, reason = "HUMAN", "Automation paused by operator safety control — all cases routed to a manager"
    elif result["sources_unavailable"] and not result["internally_resolvable"]:
        outcome, reason = "HUMAN", f"Required source(s) unavailable: {', '.join(result['sources_unavailable'])}"
    elif not result["internally_resolvable"]:
        outcome, reason = "HUMAN", f"Insufficient evidence after internal + external lookup (confidence {confidence:.2f})"
    elif confidence > threshold:
        outcome, reason = "AUTO", f"Resolved with confidence {confidence:.2f} > threshold {threshold:.2f}"
    else:
        outcome, reason = "VERIFY", f"Resolved but confidence {confidence:.2f} <= threshold {threshold:.2f}; verify before closing"

    decision = {
        "id": str(uuid.uuid4()), "customer_id": state["customer_id"],
        "gap_event_id": state["gap_event"]["id"], "outcome": outcome,
        "confidence": confidence, "reason": reason, "created_at": datetime.utcnow().isoformat(),
    }
    db.insert_decision(decision)
    return decision


def fetch_external_source_node(state: RemediationState) -> RemediationState:
    gap = state["gap_event"]
    result = state["enrichment_result"]

    if gap["gap_type"] == "MISSING_FIELD":
        _fetch_missing_fields(state["customer_id"], gap["details"]["missing_fields"], result)
    elif gap["gap_type"] == "EXPIRED_DOC":
        _fetch_expired_doc(state["customer_id"], result)
    elif gap["gap_type"] == "POLICY_MISMATCH":
        _fetch_policy_mismatch(result)
    # CHANGED_DOC / STALE_RECORD: no meaningful external source in this MVP —
    # the internal-only result stands as-is.

    state["enrichment_result"] = result
    decision = _make_decision(state, result)
    state["decision"] = decision

    attempt = state.get("retry_count", 0)
    suffix = f"_RETRY{attempt}" if attempt else ""
    log_step(
        state, agent="fetch_external_source", previous_state="ENRICHED",
        new_state=f"DECISION_{decision['outcome']}{suffix}", decision=decision["outcome"],
        confidence=decision["confidence"], evidence={"reason": decision["reason"], **result},
    )
    return state
