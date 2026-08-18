"""One channel for telling the UI what is happening.

The live panel could be driven by parsing LangGraph node names out of the
`updates` stream, and the first version was. It reported everything as the
supervisor, because deep agents run subagents as nested graphs whose node names
say nothing about which agent they belong to.

Middleware already knows. Both the ledger and the cost meter are constructed
with an `agent_name` and wrap every call that agent makes, so they can simply
say so — and custom events propagate up from nested graphs without the caller
needing `subgraphs=True`. Driving the panel from the same hooks that record the
evidence means the display cannot disagree with the audit trail: they are the
same events.

Emitting is best-effort. `get_stream_writer` raises outside a LangGraph run —
in a unit test, or under `ainvoke` rather than `astream` — and telemetry must
never be able to fail a run.
"""

from __future__ import annotations

from typing import Any


def emit(payload: dict[str, Any]) -> None:
    """Send one event to the custom stream, if anything is listening."""
    try:
        from langgraph.config import get_stream_writer

        get_stream_writer()(payload)
    except Exception:  # noqa: BLE001 — telemetry, not logic
        pass


def tool_started(agent: str, tool: str) -> None:
    emit({"event": "tool", "agent": agent, "tool": tool})


def agent_finished(agent: str) -> None:
    emit({"event": "agent_done", "agent": agent})


__all__ = ["agent_finished", "emit", "tool_started"]
