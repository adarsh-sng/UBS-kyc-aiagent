"""Mocked internal enrichment sources and the mocked 'official' verification
source. All data is fabricated for the demo — see mock_data/companies.py for
the corresponding CorporateRecord seeds these are designed to enrich.

Each source is a dict keyed by customer_id. An entry of {"__down__": True}
simulates that source being unavailable for that customer (reliability /
graceful-degradation demo scenario). Absence of a customer_id means the
source has no relevant data for that gap (not the same as being down).
"""
from __future__ import annotations

from typing import Optional

CORPORATE_REGISTRY: dict[str, dict] = {
    "continental-freight-inc": {"incorporation_cert_expiry": "2027-06-30", "confidence": 0.97},
    "granite-partners-sa": {"__down__": True},
}

TAX_DATABASE: dict[str, dict] = {
    "delta-textiles-pte": {"tax_id": "TX-DELTA-2044", "confidence": 0.96},
}

BENEFICIAL_OWNERSHIP_DB: dict[str, dict] = {
    "beacon-trading-corp": {"beneficial_owner": "Priya Raman", "confidence": 0.99},
    "granite-partners-sa": {"__down__": True},
}

PREVIOUS_KYC: dict[str, dict] = {
    "helix-ventures-bv": {"tax_id": "TX-HELIX-771", "beneficial_owner": "Helix Group Ltd", "confidence": 0.97},
    "continental-freight-inc": {"incorporation_cert_expiry": "2027-06-30", "confidence": 0.95},
    "juniper-global-trust": {"beneficial_owner": "Juniper Family Trust", "confidence": 0.7},
}

CRM: dict[str, dict] = {
    "ironwood-capital-ag": {"beneficial_owner": "Wei Chen Holdings Pte Ltd", "confidence": 0.95},
    "everline-logistics-gmbh": {"policy_acknowledged": True, "confidence": 0.99},
}

# Ground truth used only by the Verification Agent. A customer_id present
# here with a conflicting field means verification will fail for that field.
# Absence means "official source agrees with whatever was enriched" — a
# documented MVP assumption (see README, Assumptions & Trade-offs).
OFFICIAL_VERIFICATION_SOURCE: dict[str, dict] = {
    "delta-textiles-pte": {"tax_id": "TX-DELTA-9999"},
}

SOURCES: dict[str, dict[str, dict]] = {
    "corporate_registry": CORPORATE_REGISTRY,
    "tax_database": TAX_DATABASE,
    "beneficial_ownership_db": BENEFICIAL_OWNERSHIP_DB,
    "previous_kyc": PREVIOUS_KYC,
    "crm": CRM,
}


def lookup(source_name: str, customer_id: str) -> Optional[dict]:
    """Returns the source's record for this customer, or None if the source
    is unavailable/down for this customer. Returns {} if the source simply
    has no data for this customer (distinct from being down)."""
    source = SOURCES[source_name]
    entry = source.get(customer_id)
    if entry is not None and entry.get("__down__"):
        return None
    return entry or {}


def verify_against_official_source(customer_id: str, field: str, value) -> bool:
    """True if the official source agrees with `value` for `field` (or has
    no opinion, in which case we default to agreeing — see module docstring)."""
    official = OFFICIAL_VERIFICATION_SOURCE.get(customer_id, {})
    if field not in official:
        return True
    return official[field] == value
