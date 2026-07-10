"""Event dataclasses published on the in-memory EventBus (events/bus.py).

These are the four trigger/feedback event types the spec calls out:
PolicyUpdated, DocumentUploaded, ExpiryReminder, ManualReviewCompleted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class PolicyUpdated:
    new_policy_version: int
    effective_date: date = field(default_factory=date.today)


@dataclass
class DocumentUploaded:
    customer_id: str
    document_type: str
    new_hash: str
    uploaded_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ExpiryReminder:
    check_date: date = field(default_factory=date.today)


@dataclass
class ManualReviewCompleted:
    customer_id: str
    run_id: str
    rm_notes: str
    resolved_at: datetime = field(default_factory=datetime.utcnow)
