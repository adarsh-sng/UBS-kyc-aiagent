"""Pydantic entity models mirroring the SQLite schema in services/db.py."""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class RecordStatus(str, Enum):
    ACTIVE = "ACTIVE"
    UNDER_REVIEW = "UNDER_REVIEW"
    ESCALATED = "ESCALATED"


class GapType(str, Enum):
    MISSING_FIELD = "MISSING_FIELD"
    STALE_RECORD = "STALE_RECORD"
    EXPIRED_DOC = "EXPIRED_DOC"
    POLICY_MISMATCH = "POLICY_MISMATCH"
    CHANGED_DOC = "CHANGED_DOC"


class DecisionOutcome(str, Enum):
    AUTO = "AUTO"
    VERIFY = "VERIFY"
    HUMAN = "HUMAN"


class Document(BaseModel):
    type: str
    hash: str
    expiry_date: Optional[date] = None
    uploaded_at: Optional[datetime] = None


class CorporateRecord(BaseModel):
    id: str
    company_name: str
    beneficial_owner: Optional[str] = None
    tax_id: Optional[str] = None
    documents: list[Document] = Field(default_factory=list)
    policy_version: int
    status: RecordStatus = RecordStatus.ACTIVE
    parent_entity_id: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Metadata(BaseModel):
    customer_id: str
    last_checked: Optional[datetime] = None
    document_hash: Optional[str] = None
    policy_version: int
    document_expiry: Optional[date] = None
    verification_status: str = "PENDING"
    last_remediation: Optional[datetime] = None
    audit_pointer: Optional[str] = None


class GapEvent(BaseModel):
    id: str
    customer_id: str
    gap_type: GapType
    details: dict = Field(default_factory=dict)
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    trigger_source: str
    status: str = "OPEN"
    run_id: str


class Decision(BaseModel):
    id: str
    customer_id: str
    gap_event_id: Optional[str] = None
    outcome: DecisionOutcome
    confidence: float
    reason: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Audit(BaseModel):
    id: str
    customer_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    agent: str
    decision: Optional[str] = None
    evidence: dict = Field(default_factory=dict)
    confidence: Optional[float] = None
    previous_state: str
    new_state: str
    run_id: str
