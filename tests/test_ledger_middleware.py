"""The ledger decides what can be cited. What it excludes matters as much as
what it records."""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import ToolMessage

from wealth_agent.middleware.grounding_ledger import NON_EVIDENCE_TOOLS, GroundingLedgerMiddleware
from wealth_agent.data.store import RunWorkspace, is_grounded


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
    from wealth_agent.middleware.grounding_ledger import GroundingLedgerMiddleware
    from wealth_agent.data.store import GroundingLedger
    from wealth_agent.agents.registry import build_analyst_subagents

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


# --------------------------------------------------------------------------
# Prompt caching
#
# `create_deep_agent` appends `AnthropicPromptCachingMiddleware` unconditionally
# and does not deduplicate. Adding a second instance to raise the TTL therefore
# stacks rather than replaces, and Anthropic rejects the request outright:
#
#   cache_control: a ttl='1h' cache_control block must not come after a
#   ttl='5m' cache_control block.
#
# The docs say declaring it explicitly "replaces the default 5m TTL". In this
# version it does not. The failure only surfaces as a 400 on the first live
# model call, which makes it a bug you pay for twice — once in credits, once in
# the demo it interrupts — so it is worth an offline assertion.
# --------------------------------------------------------------------------


def _caching_middleware_names(middleware: list) -> list[str]:
    return [
        type(m).__name__
        for m in middleware
        if "PromptCaching" in type(m).__name__
    ]


def test_we_do_not_stack_a_second_prompt_caching_middleware() -> None:
    """Our own middleware list must not contain one; deepagents adds it."""
    from wealth_agent.data.store import GroundingLedger
    from wealth_agent.agents.common import subagent_middleware

    import tempfile
    from pathlib import Path

    ledger = GroundingLedger(Path(tempfile.mkdtemp()) / "ledger.jsonl")
    assert _caching_middleware_names(subagent_middleware(ledger, name="x")) == []


def test_deepagents_still_supplies_exactly_one_caching_middleware() -> None:
    """Pins the upstream behaviour this repo relies on.

    If a future deepagents stops appending it, caching silently disappears and
    every run costs several times more with nothing in the output to say so.
    If it ever appends two, requests start failing. Either way we want to hear
    about it from a test rather than from a bill or a 400.
    """
    from deepagents.middleware._prompt_caching import append_prompt_caching_middleware

    middleware: list = []
    append_prompt_caching_middleware(middleware)
    assert _caching_middleware_names(middleware) == ["AnthropicPromptCachingMiddleware"]


def test_no_module_declares_its_own_caching_middleware() -> None:
    """The mistake this guards against, stated directly.

    Raising the TTL by passing a second instance reads as the obvious fix — the
    docs even describe it as replacing the default — and it produces a 400 on
    the first live model call, long after the tests pass.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "wealth_agent"
    offenders = [
        path.name
        for path in src.rglob("*.py")
        if "AnthropicPromptCachingMiddleware(" in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], (
        f"{offenders} construct a caching middleware; deepagents already appends "
        f"one at a 5m TTL and does not deduplicate, so a second stacks and "
        f"Anthropic rejects the mismatched ttl ordering."
    )
