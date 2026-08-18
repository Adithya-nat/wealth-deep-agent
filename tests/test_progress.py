"""Tests for the live panel.

The bug worth guarding is the silent one. Without `subgraphs=True` a run
streams perfectly, the panel updates, the totals are right — and every
subagent's activity is simply absent, so the supervisor appears to do all the
work alone. Nothing errors, so only a test catches it.
"""

from __future__ import annotations

from wealth_agent.cli.progress import RunProgress, consume, unpack


def test_unpack_handles_both_stream_shapes() -> None:
    """`subgraphs=True` changes the tuple arity. Both must work."""
    assert unpack(("custom", {"a": 1})) == ("custom", {"a": 1})
    assert unpack((("task:abc",), "custom", {"a": 1})) == ("custom", {"a": 1})


def test_a_subagents_tool_call_creates_its_own_row() -> None:
    progress = RunProgress()
    consume(progress, "custom", {"event": "tool", "agent": "spend-analyst", "tool": "load_spend_data"})
    assert "spend-analyst" in progress.agents
    assert progress.agents["spend-analyst"].tool_calls == 1


def test_every_call_is_logged_with_its_agent() -> None:
    """The activity log answers 'what has it done', which the panel does not."""
    progress = RunProgress()
    for agent, tool in [
        ("supervisor", "write_todos"),
        ("portfolio-analyst", "load_portfolio"),
        ("market-researcher", "fetch_page"),
    ]:
        consume(progress, "custom", {"event": "tool", "agent": agent, "tool": tool})
    assert [line.agent for line in progress.log] == [
        "supervisor",
        "portfolio-analyst",
        "market-researcher",
    ]
    assert progress.log[1].note, "opaque tool names should get a plain-English note"


def test_delegation_marks_the_previous_subagent_done() -> None:
    progress = RunProgress()
    consume(progress, "custom", {"event": "tool", "agent": "spend-analyst", "tool": "monthly_trend"})
    consume(progress, "custom", {"event": "tool", "agent": "supervisor", "tool": "task"})
    assert progress.agents["spend-analyst"].status == "done"


def test_verification_appears_in_the_log() -> None:
    progress = RunProgress()
    consume(
        progress,
        "custom",
        {"event": "verification", "score": 0.969, "passed": False, "failures": 3},
    )
    assert progress.log[-1].tool == "verification"
    assert "3 to fix" in progress.log[-1].note
    assert progress.message


def test_a_passing_verification_clears_the_warning() -> None:
    progress = RunProgress()
    consume(progress, "custom", {"event": "verification", "score": 1.0, "passed": True, "failures": 0})
    assert progress.message == ""
    assert "passed" in progress.log[-1].note


def test_cost_events_update_the_footer() -> None:
    progress = RunProgress()
    consume(progress, "custom", {"event": "cost", "tokens": 500_000, "cost_usd": 0.61, "cache_hit_rate": 0.83})
    assert progress.tokens == 500_000
    assert progress.cache_hit_rate == 0.83


def test_todos_come_from_the_updates_stream() -> None:
    progress = RunProgress()
    consume(
        progress,
        "updates",
        {"node": {"todos": [{"status": "completed"}, {"status": "completed"}, {"status": "pending"}]}},
    )
    assert (progress.todos_done, progress.todos_total) == (2, 3)


def test_the_panel_renders_with_no_events_yet() -> None:
    """The first frame appears before anything has happened."""
    assert RunProgress().render(title="wealth run") is not None
