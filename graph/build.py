"""Wires the agent nodes into the LangGraph StateGraph that implements the
Decision & Orchestration Workflow:

    START -> detection -> (gap? enrichment : END)
    enrichment -> fetch_external_source
    fetch_external_source -> (AUTO/VERIFY: verification, HUMAN: manager)
    verification -> (verified: audit, retry: fetch_external_source, escalate: manager)
    manager -> communication -> rm_queue
    rm_queue -> (resolved: verification, pending: END)   [node name "rm_queue" avoids
        colliding with the "human_review" state key — see agents/human_review.py]
    audit -> END

Also owns the dispatcher-facing helpers: run_id fingerprinting (ingress
idempotency), initial-state construction, and streaming invocation so
app.py can render each agent's step as it completes.
"""
from __future__ import annotations

import hashlib
from typing import Any, Iterator

from langgraph.graph import END, StateGraph

from agents.audit import audit_node
from agents.communication import communication_node
from agents.detection import detection_node
from agents.enrichment import enrichment_node
from agents.fetch_external_source import fetch_external_source_node
from agents.human_review import human_review_node
from agents.manager import manager_node
from agents.routing import (
    route_after_detection,
    route_after_fetch_external_source,
    route_after_human_review,
    route_after_verification,
)
from agents.verification import verification_node
from models.state import RemediationState
from services import db


def build_graph():
    graph = StateGraph(RemediationState)
    graph.add_node("detection", detection_node)
    graph.add_node("enrichment", enrichment_node)
    graph.add_node("fetch_external_source", fetch_external_source_node)
    graph.add_node("verification", verification_node)
    graph.add_node("manager", manager_node)
    graph.add_node("communication", communication_node)
    graph.add_node("rm_queue", human_review_node)
    graph.add_node("audit", audit_node)

    graph.set_entry_point("detection")
    graph.add_conditional_edges(
        "detection", route_after_detection, {"gap": "enrichment", "no_gap": END}
    )
    graph.add_edge("enrichment", "fetch_external_source")
    graph.add_conditional_edges(
        "fetch_external_source", route_after_fetch_external_source,
        {"AUTO": "verification", "VERIFY": "verification", "HUMAN": "manager"},
    )
    graph.add_conditional_edges(
        "verification", route_after_verification,
        {"verified": "audit", "retry": "fetch_external_source", "escalate": "manager"},
    )
    graph.add_edge("manager", "communication")
    graph.add_edge("communication", "rm_queue")
    graph.add_conditional_edges(
        "rm_queue", route_after_human_review,
        {"resolved": "verification", "pending": END},
    )
    graph.add_edge("audit", END)

    return graph.compile()


_GRAPH = None


def get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH


def make_run_id(customer_id: str, trigger_type: str, trigger_key: str) -> str:
    """Idempotency fingerprint: same (customer, trigger type, trigger key)
    always produces the same run_id, which is how duplicate/replayed
    triggers are detected before ever invoking the graph."""
    fingerprint = f"{customer_id}:{trigger_type}:{trigger_key}"
    return hashlib.sha256(fingerprint.encode()).hexdigest()[:16]


def is_duplicate_trigger(run_id: str) -> bool:
    """Dispatcher-level guard used by the three event-bus trigger handlers
    (see app.py). A fresh manual run always gets a unique run_id, so this
    only ever short-circuits a genuinely replayed event."""
    return db.find_gap_event_by_run_id(run_id) is not None


def build_initial_state(customer_id: str, trigger_event: dict[str, Any], run_id: str) -> RemediationState:
    return RemediationState(
        customer_id=customer_id,
        trigger_event=trigger_event,
        record=db.get_record(customer_id),
        metadata=db.get_metadata(customer_id),
        gap_event=None,
        enrichment_result=None,
        decision=None,
        verification_result=None,
        human_review=None,
        assigned_manager=None,
        manager_summary=None,
        client_message_draft=None,
        retry_count=0,
        audit_trail=[],
        run_id=run_id,
        status="NEW",
    )


def run_remediation_stream(
    customer_id: str, trigger_event: dict[str, Any], run_id: str
) -> Iterator[tuple[str, RemediationState]]:
    """Streams (node_name, state_snapshot) as the graph executes, for live
    UI rendering. Starts a fresh case from the current record/metadata."""
    initial_state = build_initial_state(customer_id, trigger_event, run_id)
    for chunk in get_graph().stream(initial_state, stream_mode="updates"):
        for node_name, snapshot in chunk.items():
            yield node_name, snapshot


def resume_remediation_stream(state: RemediationState) -> Iterator[tuple[str, RemediationState]]:
    """Re-invokes the graph on a state that was previously paused at
    human_review (see agents/human_review.py::resolve_human_review)."""
    for chunk in get_graph().stream(state, stream_mode="updates"):
        for node_name, snapshot in chunk.items():
            yield node_name, snapshot
