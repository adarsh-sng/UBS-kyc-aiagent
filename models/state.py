"""LangGraph state definition for the remediation graph (graph/build.py)."""
from __future__ import annotations

from typing import Any, Optional, TypedDict


class RemediationState(TypedDict, total=False):
    customer_id: str
    trigger_event: dict[str, Any]          # {"type": "POLICY_UPDATE"|"DOCUMENT_UPLOADED"|"EXPIRY_CHECK"|"MANUAL", ...}
    record: dict[str, Any]                  # CorporateRecord snapshot (working copy)
    metadata: dict[str, Any]                # Metadata snapshot

    gap_event: Optional[dict[str, Any]]
    enrichment_result: Optional[dict[str, Any]]      # internal + external evidence merged (see agents/enrichment.py, fetch_external_source.py)
    decision: Optional[dict[str, Any]]               # produced by agents/fetch_external_source.py
    verification_result: Optional[dict[str, Any]]
    human_review: Optional[dict[str, Any]]

    assigned_manager: Optional[dict[str, Any]]        # set by agents/manager.py
    manager_summary: Optional[str]
    client_message_draft: Optional[str]               # set by agents/communication.py

    retry_count: int                          # bounded verification-retry counter (see agents/verification.py)
    audit_trail: list[dict[str, Any]]         # in-memory mirror of what's been written to SQLite this run
    run_id: str                                # idempotency fingerprint: hash(customer_id, trigger type, trigger key)
    status: str                                # NEW | RUNNING | RETRYING | ESCALATING | HUMAN_REVIEW | COMPLETED
