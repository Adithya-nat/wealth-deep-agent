"""The verifier is the load-bearing component. It gets the most tests.

Every case here is a defect the workshop claims to catch. If one of these
regresses, the demo makes a claim the code does not support.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wealth_agent.store import RunWorkspace, extract_figures, is_grounded
from wealth_agent.verify import Verdict, verify_memo


@pytest.fixture
def workspace(tmp_path: Path) -> RunWorkspace:
    ws = RunWorkspace(run_id="t", base=tmp_path)
    ws.ledger.record(
        kind="tool_result",
        name="get_account_balances",
        content=json.dumps({"total_value": 139557.05, "cash": 18420.55}),
    )
    ws.ledger.record(
        kind="tool_result",
        name="concentration",
        content=json.dumps({"symbol": "NVDA", "percent_of_portfolio": 5.64}),
    )
    ws.write_source(
        "https://example.com/nvda",
        "NVDA Q2",
        "NVIDIA reported data center revenue growth of 41% year over year.",
    )
    return ws


def _sid(ws: RunWorkspace) -> str:
    return next(iter(ws.sources()))


def test_grounded_memo_passes(workspace: RunWorkspace) -> None:
    memo = (
        f"Total value is $139,557.05 with $18,420.55 in cash. "
        f"NVDA is 5.64% of the portfolio. "
        f'The company reported "data center revenue growth of 41% year over year" '
        f"[{_sid(workspace)}]."
    )
    report = verify_memo(memo, workspace)
    assert report.passed
    assert report.score == 1.0
    assert not report.fabricated


def test_citation_to_unfetched_source_is_fabricated(workspace: RunWorkspace) -> None:
    report = verify_memo("Sector outlook is strong [src_deadbeef].", workspace)
    assert len(report.fabricated) == 1
    assert "never fetched" in report.fabricated[0].detail


def test_quote_not_in_source_is_fabricated(workspace: RunWorkspace) -> None:
    memo = f'The company said "revenue tripled in the quarter" [{_sid(workspace)}].'
    report = verify_memo(memo, workspace)
    assert len(report.fabricated) == 1
    assert "does not appear" in report.fabricated[0].detail


def test_one_citation_yields_one_finding(workspace: RunWorkspace) -> None:
    """A bad quote must not be counted twice — once grounded, once fabricated.

    Double-counting would halve the score for a single defect and inflate the
    denominator, making the headline number meaningless.
    """
    memo = f'It said "revenue tripled" [{_sid(workspace)}].'
    report = verify_memo(memo, workspace)
    assert report.checked_citations == 1


def test_invented_figure_is_unsupported(workspace: RunWorkspace) -> None:
    report = verify_memo("The position should reach $412,900.00 by year end.", workspace)
    unsupported = [f for f in report.failures if f.verdict is Verdict.UNSUPPORTED]
    assert len(unsupported) == 1


def test_reformatted_figures_still_ground(workspace: RunWorkspace) -> None:
    """Rewriting 139557.05 as $139,557.05 is formatting, not fabrication."""
    report = verify_memo("Total value is $139,557.05.", workspace)
    assert report.score == 1.0


def test_rounding_within_tolerance_grounds(workspace: RunWorkspace) -> None:
    report = verify_memo("Cash stands at $18,421.", workspace)
    assert report.score == 1.0


def test_rounding_beyond_tolerance_does_not_ground(workspace: RunWorkspace) -> None:
    """`$18,000` is not a rounding of 18420.55, it is a different claim."""
    report = verify_memo("Cash stands at $18,000.", workspace)
    assert report.score < 1.0


def test_years_and_small_counts_are_not_claims(workspace: RunWorkspace) -> None:
    report = verify_memo("As of August 2026, across 3 sectors.", workspace)
    assert report.checked_figures == 0


def test_percent_sign_makes_a_small_number_a_claim(workspace: RunWorkspace) -> None:
    """`3` is noise; `3%` is an assertion. The sign is what distinguishes them."""
    report = verify_memo("Spending fell 3% last month.", workspace)
    assert report.checked_figures == 1
    assert report.score < 1.0


def test_markdown_tables_are_skipped(workspace: RunWorkspace) -> None:
    report = verify_memo("| symbol | value |\n|---|---|\n| NVDA | 999999.99 |", workspace)
    assert report.checked_figures == 0


def test_empty_memo_scores_vacuously(workspace: RunWorkspace) -> None:
    report = verify_memo("No numbers here at all.", workspace)
    assert report.score == 1.0
    assert report.checked == 0


def test_smart_quotes_do_not_defeat_matching(workspace: RunWorkspace) -> None:
    memo = f"It reported “data center revenue growth of 41% year over year” [{_sid(workspace)}]."
    report = verify_memo(memo, workspace)
    assert not report.fabricated


def test_source_ids_are_stable_across_runs(tmp_path: Path) -> None:
    a = RunWorkspace(run_id="a", base=tmp_path).write_source("https://x.test/p", "t", "b")
    b = RunWorkspace(run_id="b", base=tmp_path).write_source("https://x.test/p", "t", "b")
    assert a.id == b.id


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$1,234.56", [1234.56]),
        ("12.5%", [12.5]),
        ("in 2026", []),
        ("across 42 shares", []),
        ("-$500.00", [-500.0]),
    ],
)
def test_figure_extraction(text: str, expected: list[float]) -> None:
    assert [f.value for f in extract_figures(text)] == expected


def test_is_grounded_matches_rounded_forms() -> None:
    grounded = {7864.5, 7864.5, 7865.0}
    assert is_grounded(7864.5, grounded)
    assert not is_grounded(7900.0, grounded)
