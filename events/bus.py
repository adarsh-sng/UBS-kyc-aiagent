"""Minimal in-memory synchronous publish-subscribe event bus.

Deliberately not durable/threaded: for this MVP the bus lives for the
lifetime of a Streamlit session (see app.py, st.session_state["event_bus"]).
A production version would back this with a durable queue.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Callable


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[type, list[Callable]] = defaultdict(list)

    def subscribe(self, event_type: type, handler: Callable) -> None:
        self._subscribers[event_type].append(handler)

    def publish(self, event: object) -> list:
        """Synchronously calls every handler registered for type(event).

        Returns the list of handler return values (used by app.py to
        collect the remediation results triggered by this event).
        """
        results = []
        for handler in self._subscribers[type(event)]:
            results.append(handler(event))
        return results
