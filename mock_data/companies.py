"""Seed dataset: ~10 mock companies, each tagged with the functional-behavior
scenario it's designed to demonstrate (see README, Sample End-to-End Traces).

Field shapes mirror models/entities.py CorporateRecord + Metadata, but a
company entry is intentionally flat here (migration/seed.py splits it into
the two tables on write).
"""
from __future__ import annotations

CURRENT_POLICY_VERSION = 2

# A record with no explicit "beneficial_owner"/"tax_id" (None) represents a
# genuinely missing mandatory field for the Detection Agent to find.
COMPANIES: list[dict] = [
    {
        "id": "acme-holdings-ltd",
        "company_name": "Acme Holdings Ltd",
        "scenario": "clean_baseline",
        "beneficial_owner": "John Okafor",
        "tax_id": "TX-ACME-1001",
        "documents": [
            {"type": "incorporation_certificate", "hash": "hash-acme-001", "expiry_date": "2028-01-01"}
        ],
        "policy_version": CURRENT_POLICY_VERSION,
        "status": "ACTIVE",
        "last_checked": "2026-07-01T09:00:00",
        "verification_status": "VERIFIED",
        "last_remediation": "2026-07-01T09:00:00",
    },
    {
        "id": "beacon-trading-corp",
        "company_name": "Beacon Trading Corp",
        "scenario": "missing_beneficial_owner_enrichable",
        "beneficial_owner": None,
        "tax_id": "TX-BEACON-2002",
        "documents": [
            {"type": "incorporation_certificate", "hash": "hash-beacon-001", "expiry_date": "2027-05-01"}
        ],
        "policy_version": CURRENT_POLICY_VERSION,
        "status": "ACTIVE",
        "last_checked": "2026-06-15T09:00:00",
        "verification_status": "PENDING",
        "last_remediation": None,
    },
    {
        "id": "continental-freight-inc",
        "company_name": "Continental Freight Inc",
        "scenario": "expired_incorporation_cert",
        "beneficial_owner": "Maria Costa",
        "tax_id": "TX-CONT-3003",
        "documents": [
            {"type": "incorporation_certificate", "hash": "hash-cont-001", "expiry_date": "2025-01-01"}
        ],
        "policy_version": CURRENT_POLICY_VERSION,
        "status": "ACTIVE",
        "last_checked": "2026-06-20T09:00:00",
        "verification_status": "VERIFIED",
        "last_remediation": "2025-01-01T09:00:00",
    },
    {
        "id": "delta-textiles-pte",
        "company_name": "Delta Textiles Pte",
        "scenario": "tax_id_mismatch",
        "beneficial_owner": "Rajiv Sharma",
        "tax_id": None,
        "documents": [
            {"type": "incorporation_certificate", "hash": "hash-delta-001", "expiry_date": "2027-03-01"}
        ],
        "policy_version": CURRENT_POLICY_VERSION,
        "status": "ACTIVE",
        "last_checked": "2026-06-18T09:00:00",
        "verification_status": "PENDING",
        "last_remediation": None,
    },
    {
        "id": "everline-logistics-gmbh",
        "company_name": "Everline Logistics GmbH",
        "scenario": "outdated_policy",
        "beneficial_owner": "Hans Mueller",
        "tax_id": "TX-EVER-5005",
        "documents": [
            {"type": "incorporation_certificate", "hash": "hash-ever-001", "expiry_date": "2028-01-01"}
        ],
        "policy_version": 1,
        "status": "ACTIVE",
        "last_checked": "2026-05-01T09:00:00",
        "verification_status": "VERIFIED",
        "last_remediation": "2026-05-01T09:00:00",
    },
    {
        "id": "falcon-resources-sa",
        "company_name": "Falcon Resources SA",
        "scenario": "unresolved_gap_needs_customer_outreach",
        "beneficial_owner": "Amara Diallo",
        "tax_id": None,
        "documents": [
            {"type": "incorporation_certificate", "hash": "hash-falcon-001", "expiry_date": "2028-01-01"}
        ],
        "policy_version": CURRENT_POLICY_VERSION,
        "status": "ACTIVE",
        "last_checked": "2026-06-25T09:00:00",
        "verification_status": "PENDING",
        "last_remediation": None,
    },
    {
        "id": "granite-partners-sa",
        "company_name": "Granite Partners SA",
        "scenario": "registry_source_down",
        "beneficial_owner": None,
        "tax_id": "TX-GRAN-7007",
        "documents": [
            {"type": "incorporation_certificate", "hash": "hash-gran-001", "expiry_date": "2028-01-01"}
        ],
        "policy_version": CURRENT_POLICY_VERSION,
        "status": "ACTIVE",
        "last_checked": "2026-06-28T09:00:00",
        "verification_status": "PENDING",
        "last_remediation": None,
    },
    {
        "id": "helix-ventures-bv",
        "company_name": "Helix Ventures BV",
        "scenario": "duplicate_trigger_idempotency",
        "beneficial_owner": "Helix Group Ltd",
        "tax_id": "TX-HELIX-771",
        "documents": [
            {"type": "incorporation_certificate", "hash": "hash-helix-OLD", "expiry_date": "2028-01-01"}
        ],
        "policy_version": CURRENT_POLICY_VERSION,
        "status": "ACTIVE",
        "last_checked": "2026-06-30T09:00:00",
        "verification_status": "VERIFIED",
        "last_remediation": "2026-06-30T09:00:00",
    },
    {
        "id": "ironwood-capital-ag",
        "company_name": "Ironwood Capital AG",
        "scenario": "parent_with_subsidiary_stretch",
        "beneficial_owner": None,
        "tax_id": "TX-IRON-9009",
        "documents": [
            {"type": "incorporation_certificate", "hash": "hash-iron-001", "expiry_date": "2028-01-01"}
        ],
        "policy_version": CURRENT_POLICY_VERSION,
        "status": "ACTIVE",
        "parent_entity_id": None,
        "last_checked": "2026-06-22T09:00:00",
        "verification_status": "PENDING",
        "last_remediation": None,
    },
    {
        "id": "juniper-global-trust",
        "company_name": "Juniper Global Trust",
        "scenario": "stale_record_and_ignored_outreach",
        "beneficial_owner": "Juniper Family Trust",
        "tax_id": "TX-JUNI-1010",
        "documents": [
            {"type": "incorporation_certificate", "hash": "hash-juni-001", "expiry_date": "2028-01-01"}
        ],
        "policy_version": CURRENT_POLICY_VERSION,
        "status": "ACTIVE",
        "last_checked": "2023-01-05T09:00:00",
        "verification_status": "PENDING",
        "last_remediation": "2023-01-05T09:00:00",
    },
]
