"""Mock Relationship Manager directory + the routing rule the Manager Agent
(agents/manager.py) uses to pick the right manager for a case. Routing is by
gap type/specialty, not arbitrary round-robin, so 'choosing the manager' is
explainable in the audit trail.
"""
from __future__ import annotations

MANAGERS = {
    "ownership": {"name": "Priya Nair", "specialty": "Beneficial Ownership", "email": "priya.nair@bank.example"},
    "tax_compliance": {"name": "David Chen", "specialty": "Tax & Policy Compliance", "email": "david.chen@bank.example"},
    "documentation": {"name": "Amara Osei", "specialty": "Documentation & Verification", "email": "amara.osei@bank.example"},
}

# gap_type -> specialty key. MISSING_FIELD is split further by which field
# is missing (see agents/manager.py::assign_manager).
GAP_TYPE_SPECIALTY = {
    "STALE_RECORD": "tax_compliance",
    "POLICY_MISMATCH": "tax_compliance",
    "EXPIRED_DOC": "documentation",
    "CHANGED_DOC": "documentation",
}


def assign_manager(gap_type: str, missing_fields: list[str] | None = None) -> dict:
    if gap_type == "MISSING_FIELD" and missing_fields:
        specialty = "ownership" if "beneficial_owner" in missing_fields else "tax_compliance"
    else:
        specialty = GAP_TYPE_SPECIALTY.get(gap_type, "documentation")
    return {**MANAGERS[specialty], "specialty_key": specialty}
