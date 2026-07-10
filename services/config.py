"""Runtime-configurable remediation thresholds, backed by the `config` table
so judges can tune them live from the Streamlit sidebar without a redeploy.
"""
from __future__ import annotations

from services import db

DEFAULTS = {
    "confidence_threshold": "0.95",
    "expiry_threshold_days": "30",
    "automation_paused": "false",
    "current_policy_version": "2",
}


def seed_defaults() -> None:
    for key, value in DEFAULTS.items():
        if db.get_config(key) is None:
            db.set_config(key, value)


def get_confidence_threshold() -> float:
    return float(db.get_config("confidence_threshold", DEFAULTS["confidence_threshold"]))


def set_confidence_threshold(value: float) -> None:
    db.set_config("confidence_threshold", str(value))


def get_expiry_threshold_days() -> int:
    return int(db.get_config("expiry_threshold_days", DEFAULTS["expiry_threshold_days"]))


def set_expiry_threshold_days(value: int) -> None:
    db.set_config("expiry_threshold_days", str(value))


def get_automation_paused() -> bool:
    return db.get_config("automation_paused", DEFAULTS["automation_paused"]) == "true"


def set_automation_paused(value: bool) -> None:
    db.set_config("automation_paused", "true" if value else "false")


def get_current_policy_version() -> int:
    return int(db.get_config("current_policy_version", DEFAULTS["current_policy_version"]))


def set_current_policy_version(value: int) -> None:
    db.set_config("current_policy_version", str(value))
