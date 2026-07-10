"""Conditional-edge predicate functions for the LangGraph topology
(graph/build.py). Kept separate from the node modules so the branching
logic that defines the Decision & Orchestration Workflow is readable in
one place.
"""
from __future__ import annotations

from typing import Literal

from models.state import RemediationState


def route_after_detection(state: RemediationState) -> Literal["gap", "no_gap"]:
    return "gap" if state.get("gap_event") else "no_gap"


def route_after_fetch_external_source(state: RemediationState) -> Literal["AUTO", "VERIFY", "HUMAN"]:
    return state["decision"]["outcome"]


def route_after_verification(state: RemediationState) -> Literal["verified", "retry", "escalate"]:
    if state["verification_result"]["verified"]:
        return "verified"
    return "retry" if state.get("status") == "RETRYING" else "escalate"


def route_after_human_review(state: RemediationState) -> Literal["resolved", "pending"]:
    hr = state.get("human_review") or {}
    return "resolved" if hr.get("status") == "RESOLVED" else "pending"
