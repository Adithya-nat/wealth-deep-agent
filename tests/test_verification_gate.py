"""Tests for the cheap-check-first gate.

The property that matters is the *negative* one: on a memo that passes, nothing
downstream runs. That is where the saving comes from, and it is invisible in a
cost report until you go looking.
"""

from __future__ import annotations

import re

from wealth_agent.config import ARTIFACTS_DIR
from wealth_agent.data.store import RunWorkspace
from wealth_agent.middleware.verification_gate import (
    MAX_REVISIONS,
    VerificationGateMiddleware,
    findings_message,
    revision_request,
)
from wealth_agent.verify import verify_memo


def _ws(label: str) -> RunWorkspace:
    return RunWorkspace(run_id=label, base=ARTIFACTS_DIR / "runs")


def test_a_clean_memo_asks_for_no_revision() -> None:
    """The saving: on a clean memo nothing downstream runs at all."""
    ws = _ws("baseline")
    assert verify_memo(ws.read_memo(), ws).passed
    report, request = revision_request(ws, 1)
    assert request is None
    assert report is not None and report.passed


def test_a_failing_memo_is_sent_back_with_the_specific_findings() -> None:
    ws = _ws("ledger-bug")
    report, request = revision_request(ws, 1)
    assert request is not None, "a 76% memo should not pass the gate"
    assert not report.passed
    text = request.content
    assert "did not pass verification" in text
    assert "line " in text, "feedback must name lines, not just a score"


def test_the_revision_loop_is_driven_by_the_caller_not_by_after_agent() -> None:
    """`after_agent` runs when the agent has already finished, so returning
    messages from it updates state and restarts nothing.

    The first version did exactly that: it detected two fabricated quotes,
    logged "revising", and ended the run with them still in the memo. Detection
    worked and the fix never fired — the most dangerous shape a control can
    take, because the logs said it acted.
    """
    import inspect

    from wealth_agent.cli import app

    source = inspect.getsource(app._run)
    assert "revision_request(" in source
    assert "revisions += 1" in source
    assert "revisions >= MAX_REVISIONS" in source, "the loop must be bounded"

    # And the middleware must no longer pretend it can drive it.
    assert "after_agent" not in VerificationGateMiddleware.__dict__, (
        "the middleware must not override after_agent — it cannot restart the agent"
    )


def test_findings_message_separates_the_two_verdicts() -> None:
    """They call for different responses, so collapsing them loses the signal."""
    ws = _ws("ledger-bug")
    text = findings_message(verify_memo(ws.read_memo(), ws), 1)
    assert "UNSUPPORTED" in text
    assert "could not verify" in text


def test_a_run_with_no_memo_is_not_treated_as_a_failure(tmp_path) -> None:
    """A crashed run has nothing to verify, which is not the same as failing.

    `base=tmp_path` matters: RunWorkspace creates its directory on construction,
    so a test that names a run id writes into the real `runs/` tree.
    """
    report, request = revision_request(RunWorkspace(run_id="empty", base=tmp_path), 1)
    assert report is None and request is None


# --------------------------------------------------------------------------
# Ceilings are runaway guards, not budgets
#
# The first version set the supervisor's ceiling at 30 because a healthy run
# used about 30. A run hit exactly 30, was stopped mid-memo, and was reported
# as a success with a grounding score. These tests exist so that cannot recur.
# --------------------------------------------------------------------------


def test_ceilings_sit_well_above_observed_healthy_usage() -> None:
    """Measured on real runs: supervisor 30, researcher 18, analysts 4.

    A ceiling anywhere near those numbers shapes normal runs instead of
    catching broken ones.
    """
    from wealth_agent.agents.common import CALL_LIMITS

    observed = {
        "supervisor": 32,
        "market-researcher": 18,
        "portfolio-analyst": 4,
        "spend-analyst": 6,
        "allocation-strategist": 4,
    }
    for agent, healthy in observed.items():
        assert CALL_LIMITS[agent] >= healthy * 2, (
            f"{agent}'s ceiling ({CALL_LIMITS[agent]}) is too close to observed "
            f"healthy usage ({healthy}) — it will truncate real runs"
        )


def test_hitting_a_ceiling_is_detected() -> None:
    """`exit_behavior='end'` raises nothing, so this is the only signal."""
    from wealth_agent.agents.common import CALL_LIMITS, truncated_agents
    from wealth_agent.models import RunMeter

    meter = RunMeter()
    usage = {"input_tokens": 10, "output_tokens": 1, "input_token_details": {}}
    for _ in range(CALL_LIMITS["supervisor"]):
        meter.record("supervisor", "claude-sonnet-5", usage)
    meter.record("spend-analyst", "claude-haiku-4-5", usage)

    assert truncated_agents(meter) == ["supervisor"]


def test_a_healthy_run_reports_no_truncation() -> None:
    from wealth_agent.agents.common import truncated_agents
    from wealth_agent.models import RunMeter

    meter = RunMeter()
    for _ in range(30):
        meter.record(
            "supervisor",
            "claude-sonnet-5",
            {"input_tokens": 10, "output_tokens": 1, "input_token_details": {}},
        )
    assert truncated_agents(meter) == []


def test_a_truncated_report_says_so_above_the_title() -> None:
    """A note at the bottom of a financial memo is a note nobody reads."""
    from wealth_agent.config import ARTIFACTS_DIR
    from wealth_agent.reporting.render import build_report_data, render_report

    ws = RunWorkspace(run_id="verified", base=ARTIFACTS_DIR / "runs")
    html = render_report(build_report_data(ws, truncated=["supervisor"]))
    assert "This review is incomplete" in html
    assert html.index('class="truncated"') < html.index("<h1>")

    clean = render_report(build_report_data(ws))
    assert "This review is incomplete" not in clean


# --------------------------------------------------------------------------
# One verification path by default, not three
# --------------------------------------------------------------------------


def test_the_llm_rubric_is_opt_in() -> None:
    """Three verification systems for one memo made runs *more* expensive.

    The gate, an unconditionally-delegated verifier subagent, and the rubric
    grader each asked for their own revision. Cost went up 40% against the
    version the change was meant to improve on. The free deterministic check
    runs by default and the LLM loop is opt-in — which is what the rest of this
    repo argues for, applied to the repo.
    """
    import inspect

    from wealth_agent.agents import supervisor

    source = inspect.getsource(supervisor.build_wealth_agent)
    assert "if always_judge:" in source
    assert source.index("VerificationGateMiddleware") < source.index("if always_judge:")


def test_the_prompt_does_not_demand_the_verifier_on_every_run() -> None:
    """The routine path is the free check. The verifier is for depth."""
    from wealth_agent import prompts

    body = re.sub(r"\s+", " ", prompts.get("supervisor_verified_suffix").body).lower()
    assert "should not delegate to `verifier` as a matter of routine" in body
    assert "you will receive a message naming each failing claim" in body


# --------------------------------------------------------------------------
# The loop itself
#
# Waiting for a real run to fail is not a test — the last three passed first
# time. This drives the same sequence the CLI drives, with the memo swapped
# underneath, so the revise-and-recheck cycle is exercised every build.
# --------------------------------------------------------------------------


def _memo_with_a_fabricated_citation() -> str:
    return (
        "# Wealth Review\n\n"
        "## Recommended actions\n\n"
        'Apple reported "a number that is definitely not on that page" '
        "[src_00000000].\n"
    )


def test_the_loop_revises_then_stops_when_the_memo_is_fixed(tmp_path) -> None:
    """Bad memo → a request naming the problem → fixed memo → no request."""
    ws = RunWorkspace(run_id="loop", base=tmp_path)
    ws.ledger.record(
        kind="tool_result",
        name="load_portfolio",
        agent="portfolio-analyst",
        args={},
        content='{"total_value": 139557.05}',
    )

    ws.write_memo(_memo_with_a_fabricated_citation())
    report, request = revision_request(ws, 1)
    assert request is not None, "a citation to a source never fetched must fail"
    assert not report.passed
    assert "FABRICATED" in request.content

    # The agent does what the message asked: drop the unsupported claim.
    ws.write_memo("# Wealth Review\n\n## Recommended actions\n\nTotal value is $139,557.05.\n")
    report, request = revision_request(ws, 2)
    assert request is None, "the fixed memo should end the loop"
    assert report.passed


def test_the_loop_is_bounded_even_if_the_memo_never_improves(tmp_path) -> None:
    """A memo that cannot be fixed by rewording must not burn the budget.

    The bound lives in the caller, so this asserts the caller has one.
    """
    import inspect

    from wealth_agent.cli import app

    ws = RunWorkspace(run_id="stuck", base=tmp_path)
    ws.write_memo(_memo_with_a_fabricated_citation())
    for attempt in (1, 2, 3):
        _report, request = revision_request(ws, attempt)
        assert request is not None, "an unfixed memo keeps failing, by design"

    source = inspect.getsource(app._run)
    assert "revisions >= MAX_REVISIONS" in source
    assert MAX_REVISIONS <= 3, "an unbounded revise loop is an unbounded bill"


def test_the_request_names_lines_so_the_agent_can_act(tmp_path) -> None:
    """'Your memo scored 0.87' is not actionable. A line number is."""
    ws = RunWorkspace(run_id="lines", base=tmp_path)
    ws.write_memo(_memo_with_a_fabricated_citation())
    _report, request = revision_request(ws, 1)
    assert "line 5" in request.content
    assert "src_00000000" in request.content
