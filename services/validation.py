"""Input validation for user-supplied free text / numeric fields (app.py).

Catches malformed 'wrong info' and defends against prompt-injection attempts
in free-text fields that a human (RM) types in — even where that text
doesn't reach an LLM call today, per the user's explicit ask for
defense-in-depth. Denylist matches are a hard reject (raise), not a silent
sanitize-and-continue: an RM note has no legitimate reason to contain
instruction-override phrasing, so forcing a human to notice and re-enter in
plain language is safer than letting a subtler variant slip through.
"""
from __future__ import annotations

import re

MAX_RM_NOTES_LEN = 2000
MAX_POLICY_VERSION_JUMP = 1000


class ValidationError(Exception):
    """Message is UI-safe — st.error(str(exc)) can be called directly."""


_INJECTION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions", re.I), "ignore previous instructions"),
    (re.compile(r"disregard\s+(your|the|prior|previous)?\s*instructions", re.I), "disregard instructions"),
    (re.compile(r"you\s+are\s+now\s+", re.I), "role override (you are now)"),
    (re.compile(r"forget\s+(everything|all\s+you|the\s+above)", re.I), "forget everything"),
    (re.compile(r"system\s+prompt", re.I), "system prompt reference"),
    (re.compile(r"^\s*System\s*:", re.I | re.M), "fake System role marker"),
    (re.compile(r"^\s*Assistant\s*:", re.I | re.M), "fake Assistant role marker"),
    (re.compile(r"\[/?CUSTOMER_DATA\]", re.I), "reserved delimiter token"),
    (re.compile(r"#{3,}"), "instruction-fence marker (###)"),
    (re.compile(r"(.)\1{9,}"), "excessive repeated-character sequence"),
    (re.compile(r"</?\s*(system|instructions?)\s*>", re.I), "fake XML-style instruction tag"),
]


def _strip_control_chars(text: str) -> str:
    return "".join(ch for ch in text if ch.isprintable() or ch in ("\n", "\t"))


def validate_rm_notes(raw: str) -> str:
    """Returns the trimmed, validated rm_notes string, or raises
    ValidationError with a specific human-readable reason."""
    if raw is None or raw.strip() == "":
        raise ValidationError("RM notes cannot be empty.")

    cleaned = _strip_control_chars(raw).strip()
    if cleaned == "":
        raise ValidationError("RM notes cannot be empty.")

    if len(cleaned) > MAX_RM_NOTES_LEN:
        raise ValidationError(
            f"RM notes are too long ({len(cleaned)} characters; max {MAX_RM_NOTES_LEN})."
        )

    for pattern, label in _INJECTION_PATTERNS:
        if pattern.search(cleaned):
            raise ValidationError(
                f"RM notes rejected: contains a disallowed pattern ({label}). "
                "Please rephrase using only factual case notes."
            )

    return cleaned


def validate_policy_version(raw, current_version: int) -> int:
    """Returns the validated int on success, else raises ValidationError."""
    if isinstance(raw, float) and not raw.is_integer():
        raise ValidationError("Policy version must be a whole number.")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValidationError("Policy version must be an integer.")

    if value <= current_version:
        raise ValidationError(
            f"New policy version ({value}) must be greater than the current version ({current_version})."
        )
    if value > current_version + MAX_POLICY_VERSION_JUMP:
        raise ValidationError(
            f"New policy version ({value}) looks like a typo — more than "
            f"{MAX_POLICY_VERSION_JUMP} past the current version ({current_version})."
        )
    return value
