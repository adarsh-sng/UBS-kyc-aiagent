"""One-time (but safely re-runnable) batch migration.

Indexes the mock 'existing manual KYC database' (mock_data/companies.py)
into corporate_records + metadata. This metadata table is what the
Continuous Remediation Loop's triggers watch (see events/ + agents/detection.py).

Usage:
    python -m migration.seed            # seed/refresh companies + config
    python -m migration.seed --reset    # also clears gap_events/decisions/audits
"""
from __future__ import annotations

import sys

from mock_data.companies import COMPANIES
from services import config, db


def _split_record_and_metadata(company: dict) -> tuple[dict, dict]:
    record = {
        "id": company["id"],
        "company_name": company["company_name"],
        "beneficial_owner": company.get("beneficial_owner"),
        "tax_id": company.get("tax_id"),
        "documents": company.get("documents", []),
        "policy_version": company["policy_version"],
        "status": company.get("status", "ACTIVE"),
        "parent_entity_id": company.get("parent_entity_id"),
    }
    primary_doc = (company.get("documents") or [{}])[0]
    metadata = {
        "customer_id": company["id"],
        "last_checked": company.get("last_checked"),
        "document_hash": primary_doc.get("hash"),
        "policy_version": company["policy_version"],
        "document_expiry": primary_doc.get("expiry_date"),
        "verification_status": company.get("verification_status", "PENDING"),
        "last_remediation": company.get("last_remediation"),
        "audit_pointer": None,
    }
    return record, metadata


def seed(reset: bool = False) -> None:
    db.init_db()
    config.seed_defaults()

    for company in COMPANIES:
        record, metadata = _split_record_and_metadata(company)
        db.upsert_record(record)          # parent table first (FK dependency)
        db.upsert_metadata(metadata)

    if reset:
        db.truncate_transactional_tables()


if __name__ == "__main__":
    seed(reset="--reset" in sys.argv)
    print(f"Seeded {len(COMPANIES)} companies into {db.DB_PATH}")
