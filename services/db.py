"""SQLite persistence layer.

All entity state (CorporateRecord, Metadata, GapEvent, Decision, Audit) plus
runtime config lives here. Connections are opened per-call (sqlite3 is cheap
for this MVP's scale) so this module is safe to call from Streamlit's
rerun-per-interaction model without connection-lifecycle headaches.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

DB_PATH = Path(__file__).resolve().parent.parent / "kyc.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS corporate_records (
    id TEXT PRIMARY KEY,
    company_name TEXT NOT NULL,
    beneficial_owner TEXT,
    tax_id TEXT,
    documents TEXT NOT NULL DEFAULT '[]',
    policy_version INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    parent_entity_id TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS metadata (
    customer_id TEXT PRIMARY KEY REFERENCES corporate_records(id),
    last_checked TEXT,
    document_hash TEXT,
    policy_version INTEGER,
    document_expiry TEXT,
    verification_status TEXT,
    last_remediation TEXT,
    audit_pointer TEXT
);

CREATE TABLE IF NOT EXISTS gap_events (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    gap_type TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '{}',
    detected_at TEXT NOT NULL,
    trigger_source TEXT,
    status TEXT NOT NULL DEFAULT 'OPEN',
    run_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    gap_event_id TEXT,
    outcome TEXT NOT NULL,
    confidence REAL,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audits (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    agent TEXT NOT NULL,
    decision TEXT,
    evidence TEXT NOT NULL DEFAULT '{}',
    confidence REAL,
    previous_state TEXT,
    new_state TEXT,
    run_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(SCHEMA)


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


# ---------------------------------------------------------------- records --
def upsert_record(record: dict[str, Any]) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO corporate_records
               (id, company_name, beneficial_owner, tax_id, documents,
                policy_version, status, parent_entity_id, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record["id"],
                record["company_name"],
                record.get("beneficial_owner"),
                record.get("tax_id"),
                json.dumps(record.get("documents", [])),
                record["policy_version"],
                record.get("status", "ACTIVE"),
                record.get("parent_entity_id"),
                datetime.utcnow().isoformat(),
            ),
        )


def get_record(customer_id: str) -> Optional[dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM corporate_records WHERE id = ?", (customer_id,)
        ).fetchone()
        if row is None:
            return None
        d = _row_to_dict(row)
        d["documents"] = json.loads(d["documents"])
        return d


def list_records_with_metadata() -> list[dict[str, Any]]:
    """Customer table for the UI: corporate_records left-joined with metadata
    and the most recent decision outcome (for status badges)."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT r.*, m.last_checked, m.document_hash, m.document_expiry,
                   m.verification_status, m.last_remediation,
                   (SELECT outcome FROM decisions d WHERE d.customer_id = r.id
                    ORDER BY d.created_at DESC LIMIT 1) AS last_outcome,
                   (SELECT status FROM gap_events g WHERE g.customer_id = r.id
                    ORDER BY g.detected_at DESC LIMIT 1) AS last_gap_status
            FROM corporate_records r
            LEFT JOIN metadata m ON m.customer_id = r.id
            ORDER BY r.company_name
            """
        ).fetchall()
        results = []
        for row in rows:
            d = _row_to_dict(row)
            d["documents"] = json.loads(d["documents"])
            results.append(d)
        return results


# --------------------------------------------------------------- metadata --
def upsert_metadata(metadata: dict[str, Any]) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO metadata
               (customer_id, last_checked, document_hash, policy_version,
                document_expiry, verification_status, last_remediation, audit_pointer)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                metadata["customer_id"],
                metadata.get("last_checked"),
                metadata.get("document_hash"),
                metadata.get("policy_version"),
                metadata.get("document_expiry"),
                metadata.get("verification_status", "PENDING"),
                metadata.get("last_remediation"),
                metadata.get("audit_pointer"),
            ),
        )


def get_metadata(customer_id: str) -> Optional[dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM metadata WHERE customer_id = ?", (customer_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None


def list_metadata() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM metadata").fetchall()
        return [_row_to_dict(r) for r in rows]


# -------------------------------------------------------------- gap_events --
def insert_gap_event(gap_event: dict[str, Any]) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO gap_events
               (id, customer_id, gap_type, details, detected_at, trigger_source, status, run_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                gap_event["id"],
                gap_event["customer_id"],
                gap_event["gap_type"],
                json.dumps(gap_event.get("details", {})),
                gap_event["detected_at"],
                gap_event.get("trigger_source"),
                gap_event.get("status", "OPEN"),
                gap_event["run_id"],
            ),
        )


def find_gap_event_by_run_id(run_id: str) -> Optional[dict[str, Any]]:
    """Idempotency check: has this exact trigger fingerprint already been processed?"""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM gap_events WHERE run_id = ? LIMIT 1", (run_id,)
        ).fetchone()
        if row is None:
            return None
        d = _row_to_dict(row)
        d["details"] = json.loads(d["details"])
        return d


def set_gap_event_status(gap_event_id: str, status: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE gap_events SET status = ? WHERE id = ?", (status, gap_event_id)
        )


# --------------------------------------------------------------- decisions --
def insert_decision(decision: dict[str, Any]) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO decisions
               (id, customer_id, gap_event_id, outcome, confidence, reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                decision["id"],
                decision["customer_id"],
                decision.get("gap_event_id"),
                decision["outcome"],
                decision.get("confidence"),
                decision.get("reason"),
                decision.get("created_at", datetime.utcnow().isoformat()),
            ),
        )


# ------------------------------------------------------------------ audits --
def log_audit(audit: dict[str, Any]) -> bool:
    """Insert an audit row, deduping on (run_id, agent, new_state).

    Returns True if a new row was written, False if it was a duplicate
    (this is the idempotency guard against replayed/duplicate triggers).
    """
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT 1 FROM audits WHERE run_id = ? AND agent = ? AND new_state = ?",
            (audit["run_id"], audit["agent"], audit["new_state"]),
        ).fetchone()
        if existing:
            return False
        conn.execute(
            """INSERT INTO audits
               (id, customer_id, timestamp, agent, decision, evidence,
                confidence, previous_state, new_state, run_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                audit.get("id") or str(uuid.uuid4()),
                audit["customer_id"],
                audit.get("timestamp", datetime.utcnow().isoformat()),
                audit["agent"],
                audit.get("decision"),
                json.dumps(audit.get("evidence", {})),
                audit.get("confidence"),
                audit.get("previous_state"),
                audit.get("new_state"),
                audit["run_id"],
            ),
        )
        return True


def get_audit_trail(customer_id: str) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM audits WHERE customer_id = ? ORDER BY timestamp ASC",
            (customer_id,),
        ).fetchall()
        results = []
        for row in rows:
            d = _row_to_dict(row)
            d["evidence"] = json.loads(d["evidence"])
            results.append(d)
        return results


def replay_audit(customer_id: str) -> list[dict[str, Any]]:
    """Alias emphasizing the replay use case: an ordered, side-effect-free
    reconstruction of every action taken on this customer's record."""
    return get_audit_trail(customer_id)


# ------------------------------------------------------------------ config --
def get_config(key: str, default: str | None = None) -> str | None:
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_config(key: str, value: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value)
        )


# ------------------------------------------------------------------- reset --
def truncate_transactional_tables() -> None:
    """Clears gap_events/decisions/audits so a demo can be re-run from a
    clean slate without re-seeding the company records themselves."""
    with get_connection() as conn:
        conn.execute("DELETE FROM gap_events")
        conn.execute("DELETE FROM decisions")
        conn.execute("DELETE FROM audits")
