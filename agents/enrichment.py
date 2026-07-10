"""Enrichment Agent — internal-only pass.

Goal: avoid contacting the customer (or even reaching out externally)
whenever possible. Tries mocked sources we already hold *on file*
(Previous KYC, CRM) first. Anything still unresolved is handed to the
Fetch External Source Agent (agents/fetch_external_source.py), which
tries external registries and makes the AUTO/VERIFY/HUMAN call.

Input: gap_event, record.
Output: enrichment_result = {updated_fields, confidence, evidence,
sources_used, sources_unavailable, internally_resolvable}.
Downstream: Fetch External Source Agent.
"""
from __future__ import annotations

from agents.audit import log_step
from models.state import RemediationState
from services import mock_sources

INTERNAL_FIELD_SOURCES = {
    "beneficial_owner": ["crm"],
    "tax_id": ["previous_kyc"],
}


def _empty_result() -> dict:
    return {
        "updated_fields": {},
        "confidence": 0.0,
        "evidence": {},
        "sources_used": [],
        "sources_unavailable": [],
        "internally_resolvable": False,
    }


def _enrich_missing_fields(customer_id: str, missing_fields: list[str], result: dict) -> None:
    confidence = 0.0
    for field in missing_fields:
        for source_name in INTERNAL_FIELD_SOURCES.get(field, []):
            record = mock_sources.lookup(source_name, customer_id)
            if record is None:
                result["sources_unavailable"].append(source_name)
                continue
            if field in record:
                result["updated_fields"][field] = record[field]
                result["evidence"][field] = {
                    "source": source_name, "value": record[field],
                    "confidence": record.get("confidence", 0.9),
                }
                result["sources_used"].append(source_name)
                confidence = max(confidence, record.get("confidence", 0.9))
                break
    result["confidence"] = confidence
    result["internally_resolvable"] = all(f in result["updated_fields"] for f in missing_fields)


def _enrich_expired_doc(customer_id: str, result: dict) -> None:
    record = mock_sources.lookup("previous_kyc", customer_id)
    if record is None:
        result["sources_unavailable"].append("previous_kyc")
        return
    if "incorporation_cert_expiry" in record:
        result["updated_fields"]["document_expiry"] = record["incorporation_cert_expiry"]
        result["evidence"]["document_expiry"] = {
            "source": "previous_kyc", "value": record["incorporation_cert_expiry"],
            "confidence": record.get("confidence", 0.9),
        }
        result["sources_used"].append("previous_kyc")
        result["confidence"] = record.get("confidence", 0.9)
        result["internally_resolvable"] = True


def _enrich_policy_mismatch(customer_id: str, result: dict) -> None:
    from services import config  # local import avoids a module-level cycle with services.config

    record = mock_sources.lookup("crm", customer_id)
    if record is None:
        result["sources_unavailable"].append("crm")
        return
    if record.get("policy_acknowledged"):
        result["updated_fields"]["policy_version"] = config.get_current_policy_version()
        result["evidence"]["policy_version"] = {
            "source": "crm", "value": "policy acknowledgement on file",
            "confidence": record.get("confidence", 0.9),
        }
        result["sources_used"].append("crm")
        result["confidence"] = record.get("confidence", 0.9)
        result["internally_resolvable"] = True


def _enrich_changed_doc(state: RemediationState, result: dict) -> None:
    customer_id = state["customer_id"]
    new_hash = (state.get("trigger_event") or {}).get("new_hash")
    record = mock_sources.lookup("previous_kyc", customer_id)
    if record is None:
        result["sources_unavailable"].append("previous_kyc")
        return
    result["updated_fields"]["document_hash"] = new_hash
    result["evidence"]["document_hash"] = {
        "source": "previous_kyc",
        "note": "new upload cross-checked against last known-good KYC record",
        "confidence": record.get("confidence", 0.9),
    }
    result["sources_used"].append("previous_kyc")
    result["confidence"] = record.get("confidence", 0.9)
    result["internally_resolvable"] = True


def _enrich_stale_record(customer_id: str, result: dict) -> None:
    # Staleness is about re-attestation, not a resolvable data field — an
    # internal source can lend supporting confidence but can't fully close
    # a stale-record gap without a human/RM-driven re-attestation.
    record = mock_sources.lookup("previous_kyc", customer_id)
    if record is None:
        result["sources_unavailable"].append("previous_kyc")
        result["confidence"] = 0.0
    else:
        result["confidence"] = record.get("confidence", 0.4) if record else 0.4
    result["internally_resolvable"] = False


def enrichment_node(state: RemediationState) -> RemediationState:
    # Resume path: human review already supplied high-confidence values —
    # don't let a fresh internal-source lookup clobber them.
    if state.get("human_review") and state["human_review"].get("status") == "RESOLVED":
        log_step(
            state, agent="enrichment", previous_state="GAP_DETECTED", new_state="ENRICHED",
            confidence=(state.get("enrichment_result") or {}).get("confidence"),
            evidence={"note": "enrichment result carried over from human review resolution"},
        )
        return state

    gap = state["gap_event"]
    customer_id = state["customer_id"]
    result = _empty_result()

    if gap["gap_type"] == "MISSING_FIELD":
        _enrich_missing_fields(customer_id, gap["details"]["missing_fields"], result)
    elif gap["gap_type"] == "EXPIRED_DOC":
        _enrich_expired_doc(customer_id, result)
    elif gap["gap_type"] == "POLICY_MISMATCH":
        _enrich_policy_mismatch(customer_id, result)
    elif gap["gap_type"] == "CHANGED_DOC":
        _enrich_changed_doc(state, result)
    elif gap["gap_type"] == "STALE_RECORD":
        _enrich_stale_record(customer_id, result)

    state["enrichment_result"] = result
    log_step(
        state, agent="enrichment", previous_state="GAP_DETECTED", new_state="ENRICHED",
        confidence=result["confidence"], evidence=result,
    )
    return state
