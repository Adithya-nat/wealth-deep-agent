"""The ledger decides what can be cited. What it excludes matters as much as
what it records."""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import ToolMessage

from wealth_agent.ledger_middleware import NON_EVIDENCE_TOOLS, GroundingLedgerMiddleware
from wealth_agent.store import RunWorkspace, is_grounded


class _Request:
    """Minimal stand-in for ToolCallRequest — only `tool_call` is read."""

    def __init__(self, name: str, args: dict | None = None) -> None:
        self.tool_call = {"name": name, "args": args or {}}


@pytest.fixture
def workspace(tmp_path: Path) -> RunWorkspace:
    return RunWorkspace(run_id="ledger", base=tmp_path)


def _call(mw: GroundingLedgerMiddleware, name: str, content: str) -> None:
    mw.wrap_tool_call(_Request(name), lambda _r: ToolMessage(content=content, tool_call_id="1"))


def test_evidence_tools_are_recorded(workspace: RunWorkspace) -> None:
    mw = GroundingLedgerMiddleware(workspace.ledger, agent_name="portfolio-analyst")
    _call(mw, "get_positions", '{"market_value": 7864.5}')
    entries = list(workspace.ledger.entries())
    assert len(entries) == 1
    assert entries[0].agent == "portfolio-analyst"
    assert is_grounded(7864.5, workspace.grounded_values())


@pytest.mark.parametrize("tool_name", sorted(NON_EVIDENCE_TOOLS))
def test_bookkeeping_tools_are_not_recorded(
    workspace: RunWorkspace, tool_name: str
) -> None:
    mw = GroundingLedgerMiddleware(workspace.ledger)
    _call(mw, tool_name, '{"anything": 12345.67}')
    assert list(workspace.ledger.entries()) == []


def test_reading_a_skill_does_not_ground_its_examples(workspace: RunWorkspace) -> None:
    """The bug this guards against was invisible and self-defeating.

    `skills/memo-format/SKILL.md` contains `"roughly $140,000"` as a *negative*
    example of rounding past tolerance. Recording the agent's read of that file
    put $140,000 into the grounded set, so the exact defect the skill warns
    against would have verified successfully.

    Instructions are not evidence.
    """
    mw = GroundingLedgerMiddleware(workspace.ledger)
    _call(mw, "read_file", 'Bad: "roughly $140,000" — you rounded. $139,557.05 is correct.')
    assert not is_grounded(140000.0, workspace.grounded_values())
    assert not is_grounded(139557.05, workspace.grounded_values())


def test_empty_results_are_skipped(workspace: RunWorkspace) -> None:
    mw = GroundingLedgerMiddleware(workspace.ledger)
    _call(mw, "get_positions", "")
    assert list(workspace.ledger.entries()) == []


def test_the_ledger_survives_interleaved_writers(workspace: RunWorkspace) -> None:
    """Subagents record concurrently; append mode must not lose entries."""
    a = GroundingLedgerMiddleware(workspace.ledger, agent_name="portfolio-analyst")
    b = GroundingLedgerMiddleware(workspace.ledger, agent_name="spend-analyst")
    for i in range(20):
        _call(a if i % 2 else b, "get_positions", f'{{"n": {i}}}')
    assert len(list(workspace.ledger.entries())) == 20
    assert set(workspace.ledger.by_agent()) == {"portfolio-analyst", "spend-analyst"}


async def test_every_subagent_records_to_the_ledger() -> None:
    """Regression: declarative subagents do not inherit parent middleware.

    Installing the recorder only on the supervisor produced a system that ran
    perfectly and reported the actual cash balance as unsupported, because no
    subagent's tool results ever reached the ledger. Nothing errored — the
    evidence was simply destroyed at the boundary between context windows.

    This asserts on configuration rather than on a live run so it costs no
    tokens and fails at the moment someone adds a fourth subagent.
    """
    from wealth_agent.ledger_middleware import GroundingLedgerMiddleware
    from wealth_agent.store import GroundingLedger
    from wealth_agent.subagents import build_analyst_subagents

    import tempfile

    ledger = GroundingLedger(Path(tempfile.mkdtemp()) / "ledger.jsonl")
    subagents = build_analyst_subagents(
        portfolio_tools=[],
        spend_tools=[],
        research_tools=[],
        model="anthropic:claude-sonnet-4-6",
        ledger=ledger,
    )
    assert subagents, "expected declarative subagents"
    for spec in subagents:
        recorders = [
            m for m in spec.get("middleware", []) if isinstance(m, GroundingLedgerMiddleware)
        ]
        assert len(recorders) == 1, f"{spec['name']} is not recording to the ledger"
        # Entries must be attributable, or "which subagent saw this number?"
        # becomes unanswerable at exactly the moment it matters.
        assert recorders[0].agent_name == spec["name"]
