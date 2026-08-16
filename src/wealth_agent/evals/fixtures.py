"""Labeled memos: the ground truth that makes judge alignment possible.

Every fixture is a memo written against one fixed body of evidence, with a
human label saying whether it is grounded and — when it is not — exactly which
defect was planted.

Two properties matter and are easy to lose:

**The evidence is shared and deterministic.** All fixtures verify against the
same workspace, built from :mod:`wealth_agent.synthetic`. So a fixture labeled
"grounded" really is grounded — you can prove it by running the deterministic
checker over it, which :mod:`wealth_agent.evals.judge_alignment` does as a
self-test before it grades anything.

**The defects are subtle.** A judge that catches `$999,999,999.00` tells you
nothing. These are the errors that survive review: a right number against a
wrong denominator, a real citation attached to the wrong claim, a paraphrase
inside quotation marks, a partial month compared against full ones. Each one is
a real failure mode of the pipeline in ``supervisor.py``, not a synthetic
puzzle.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from wealth_agent import synthetic as syn
from wealth_agent.store import RunWorkspace

#: URL used for the fixture source, so its id is stable across machines.
FIXTURE_SOURCE_URL = "https://example.test/nvda-q2-fy2027"

FIXTURE_SOURCE_TEXT = """\
# NVIDIA Q2 FY2027 Results

NVIDIA today reported revenue for the second quarter of $52.4 billion, up 41%
from a year ago. Data center revenue was $41.1 billion. The company said demand
for its Blackwell architecture "continues to exceed supply", and guided third
quarter revenue to approximately $56.0 billion.

Gross margin was 74.6% on a GAAP basis. The board approved an additional
$25.0 billion in share repurchase authorization.
"""


class Defect:
    """The failure modes worth measuring a judge against."""

    NONE = "none"
    ROUNDED_PAST_TOLERANCE = "rounded_past_tolerance"
    INVENTED_FIGURE = "invented_figure"
    FABRICATED_CITATION = "fabricated_citation"
    MISATTRIBUTED_QUOTE = "misattributed_quote"
    PARAPHRASE_IN_QUOTES = "paraphrase_in_quotes"
    WRONG_DENOMINATOR = "wrong_denominator"
    PARTIAL_MONTH_TREND = "partial_month_trend"
    UNSOURCED_PERCENTAGE = "unsourced_percentage"


@dataclass(frozen=True)
class MemoFixture:
    """One labeled memo.

    Attributes:
        id: Stable identifier, used as the LangSmith example name.
        memo: The memo text.
        grounded: The human label. ``True`` means every claim holds up.
        defect: Which failure was planted, or :data:`Defect.NONE`.
        note: Why a human labeled it this way — the reasoning a judge prompt
            needs to learn, and the thing you lose if you only store the label.
    """

    id: str
    memo: str
    grounded: bool
    defect: str
    note: str


def build_evidence_workspace(base: Path) -> RunWorkspace:
    """Build the fixed evidence every fixture is checked against.

    Mirrors what a real run records: portfolio and spend tool results, plus one
    fetched source. Deterministic, so the reference labels stay valid.
    """
    ws = RunWorkspace(run_id="fixtures", base=base)
    portfolio = syn.build_portfolio()

    ws.ledger.record(
        kind="tool_result",
        name="load_portfolio",
        agent="portfolio-analyst",
        content=json.dumps(
            {
                "positions_loaded": len(portfolio.positions),
                "total_value": portfolio.total_value,
                "cash": portfolio.cash,
                "as_of": portfolio.as_of.isoformat(),
            }
        ),
    )
    ws.ledger.record(
        kind="tool_result",
        name="concentration",
        agent="portfolio-analyst",
        content=json.dumps(
            {
                "denominator": "total_value_including_cash",
                "total_value": portfolio.total_value,
                "positions": portfolio.concentration(),
            }
        ),
    )
    ws.ledger.record(
        kind="tool_result",
        name="sector_exposure",
        agent="portfolio-analyst",
        content=json.dumps(
            {
                "denominator": "equity_only",
                "equity_value": portfolio.equity_value,
                "sectors": [
                    {
                        "sector": sector,
                        "market_value": value,
                        "percent_of_equity": round(
                            100 * value / portfolio.equity_value, 2
                        ),
                    }
                    for sector, value in portfolio.by_sector().items()
                ],
            }
        ),
    )
    ws.ledger.record(
        kind="tool_result",
        name="unrealized_pl_summary",
        agent="portfolio-analyst",
        content=json.dumps(
            {
                "total_cost_basis": portfolio.total_cost_basis,
                "total_market_value": portfolio.equity_value,
                "total_unrealized_pl": portfolio.total_unrealized_pl,
            }
        ),
    )
    ws.ledger.record(
        kind="tool_result",
        name="spending_by_category",
        agent="spend-analyst",
        content=json.dumps(
            {
                "total_spend": 27946.37,
                "transaction_count": 251,
                "by_category": [
                    {"category": "Shopping", "total": 8213.44, "percent_of_spend": 29.39},
                    {"category": "Travel", "total": 5104.90, "percent_of_spend": 18.27},
                    {"category": "Dining", "total": 3877.12, "percent_of_spend": 13.87},
                    {"category": "Groceries", "total": 3320.08, "percent_of_spend": 11.88},
                ],
            }
        ),
    )
    ws.ledger.record(
        kind="tool_result",
        name="find_recurring_charges",
        agent="spend-analyst",
        content=json.dumps(
            {
                "recurring_charges": [
                    {"merchant": "Equinox", "typical_amount": 265.00,
                     "estimated_annual_cost": 3180.00},
                    {"merchant": "State Farm", "typical_amount": 178.44,
                     "estimated_annual_cost": 2141.28},
                    {"merchant": "PG&E", "typical_amount": 141.08,
                     "estimated_annual_cost": 1692.96},
                ],
                "total_estimated_annual_cost": 9622.20,
            }
        ),
    )
    ws.write_source(FIXTURE_SOURCE_URL, "NVIDIA Q2 FY2027 Results", FIXTURE_SOURCE_TEXT)
    return ws


def _sid() -> str:
    from wealth_agent.store import source_id

    return source_id(FIXTURE_SOURCE_URL)


def build_fixtures() -> list[MemoFixture]:
    """The labeled set. Balanced, so a judge cannot score well by guessing."""
    sid = _sid()
    portfolio = syn.build_portfolio()
    total = portfolio.total_value
    nvda = next(p for p in portfolio.concentration() if p["symbol"] == "NVDA")
    voo = next(p for p in portfolio.concentration() if p["symbol"] == "VOO")

    grounded: list[MemoFixture] = [
        MemoFixture(
            id="g01-portfolio-basics",
            memo=(
                f"## Portfolio\n"
                f"Total value is ${total:,.2f}, including ${portfolio.cash:,.2f} in cash.\n"
                f"The largest holding is VOO at {voo['percent_of_portfolio']}% of total "
                f"value including cash."
            ),
            grounded=True,
            defect=Defect.NONE,
            note="Every figure came from load_portfolio or concentration, and the "
            "percentage states its denominator.",
        ),
        MemoFixture(
            id="g02-quote-exact",
            memo=(
                f"## Market context\n"
                f'NVIDIA said demand for Blackwell "continues to exceed supply" [{sid}].'
            ),
            grounded=True,
            defect=Defect.NONE,
            note="The quoted span appears verbatim in the fetched source.",
        ),
        MemoFixture(
            id="g03-paraphrase-cited",
            memo=(
                f"## Market context\n"
                f"NVIDIA guided third quarter revenue to roughly $56.0 billion [{sid}]."
            ),
            grounded=True,
            defect=Defect.NONE,
            note="Paraphrase without quotation marks, cited, and the figure is in "
            "the source.",
        ),
        MemoFixture(
            id="g04-spend-totals",
            memo=(
                "## Spending\n"
                "Total spend across 251 charges was $27,946.37. Shopping led at "
                "$8,213.44, or 29.39% of spend."
            ),
            grounded=True,
            defect=Defect.NONE,
            note="Both the total and the share came from spending_by_category.",
        ),
        MemoFixture(
            id="g05-recurring",
            memo=(
                "## Commitments\n"
                "Recurring charges run $9,622.20 a year on current pricing, led by "
                "Equinox at $265.00 a month."
            ),
            grounded=True,
            defect=Defect.NONE,
            note="From find_recurring_charges, and the extrapolation is flagged as "
            "'on current pricing'.",
        ),
        MemoFixture(
            id="g06-honest-gap",
            memo=(
                "## What we could not verify\n"
                "We have no data on retirement accounts held elsewhere, so the "
                "allocation above covers the brokerage account only."
            ),
            grounded=True,
            defect=Defect.NONE,
            note="States a limitation without asserting any unverifiable figure.",
        ),
        MemoFixture(
            id="g07-unrealized",
            memo=(
                f"## Portfolio\n"
                f"Unrealized gain is ${portfolio.total_unrealized_pl:,.2f} against a "
                f"cost basis of ${portfolio.total_cost_basis:,.2f}."
            ),
            grounded=True,
            defect=Defect.NONE,
            note="Both figures came from unrealized_pl_summary.",
        ),
        MemoFixture(
            id="g08-reformatted",
            memo=(
                f"## Portfolio\n"
                f"NVDA represents {nvda['percent_of_portfolio']}% of total value "
                f"including cash, worth ${nvda['market_value']:,.2f}."
            ),
            grounded=True,
            defect=Defect.NONE,
            note="Reformatting a tool's number with separators is not fabrication.",
        ),
        MemoFixture(
            id="g09-quote-and-figure",
            memo=(
                f"## Market context\n"
                f"Data center revenue was $41.1 billion in the quarter [{sid}], and "
                f"the company said demand \"continues to exceed supply\" [{sid}]."
            ),
            grounded=True,
            defect=Defect.NONE,
            note="Both the figure and the quote are in the fetched source.",
        ),
        MemoFixture(
            id="g10-no-numbers",
            memo=(
                "## Summary\n"
                "The portfolio is concentrated in technology and the spending "
                "profile is stable. Nothing requires action this month."
            ),
            grounded=True,
            defect=Defect.NONE,
            note="A qualitative claim with no figures is vacuously grounded. Whether "
            "it is *useful* is a different question, and not this checker's job.",
        ),
    ]

    ungrounded: list[MemoFixture] = [
        MemoFixture(
            id="u01-rounded-past-tolerance",
            memo="## Portfolio\nTotal value is roughly $140,000.",
            grounded=False,
            defect=Defect.ROUNDED_PAST_TOLERANCE,
            note=f"Actual is ${total:,.2f}. Rounding to $140,000 is a different "
            "claim, not a reformatting — no tool returned it.",
        ),
        MemoFixture(
            id="u02-invented-forward-figure",
            memo=(
                "## Portfolio\n"
                "On current trends the portfolio should reach $186,400.00 by year end."
            ),
            grounded=False,
            defect=Defect.INVENTED_FIGURE,
            note="A forecast no tool produced. Plausible, precise, and entirely "
            "invented — the most dangerous shape of error.",
        ),
        MemoFixture(
            id="u03-fabricated-citation",
            memo=(
                "## Market context\n"
                "Analysts expect semiconductor demand to stay strong through 2027 "
                "[src_4b1c9de2]."
            ),
            grounded=False,
            defect=Defect.FABRICATED_CITATION,
            note="src_4b1c9de2 was never fetched. The claim may be true; the "
            "citation is hollow.",
        ),
        MemoFixture(
            id="u04-misattributed-quote",
            memo=(
                f"## Market context\n"
                f'NVIDIA said it expects "margins to compress in the second half" [{sid}].'
            ),
            grounded=False,
            defect=Defect.MISATTRIBUTED_QUOTE,
            note="The source says nothing of the kind. This is what attribution "
            "drift looks like after a subagent compresses several sources.",
        ),
        MemoFixture(
            id="u05-paraphrase-in-quotes",
            memo=(
                f"## Market context\n"
                f'The company reported that "revenue grew 41 percent year over year" '
                f"[{sid}]."
            ),
            grounded=False,
            defect=Defect.PARAPHRASE_IN_QUOTES,
            note="The source says 'up 41% from a year ago'. The meaning survives; "
            "the quotation marks assert words that were not used.",
        ),
        MemoFixture(
            id="u06-wrong-denominator",
            memo=(
                "## Portfolio\n"
                "Information Technology is 34.71% of the portfolio."
            ),
            grounded=False,
            defect=Defect.WRONG_DENOMINATOR,
            note="sector_exposure is a share of equity only. Stated against 'the "
            "portfolio' it reads as including cash, which is a different number.",
        ),
        MemoFixture(
            id="u07-partial-month-trend",
            memo=(
                "## Spending\n"
                "Spending fell 46% in August, the sharpest drop in six months."
            ),
            grounded=False,
            defect=Defect.PARTIAL_MONTH_TREND,
            note="August data ends mid-month. Comparing a partial month to full "
            "ones manufactures a trend, and no tool returned 46%.",
        ),
        MemoFixture(
            id="u08-unsourced-percentage",
            memo=(
                "## Spending\n"
                "Discretionary spending is up 12% versus the prior quarter."
            ),
            grounded=False,
            defect=Defect.UNSOURCED_PERCENTAGE,
            note="No tool computed a discretionary-spend comparison. The figure has "
            "no origin.",
        ),
        MemoFixture(
            id="u09-mixed-good-and-bad",
            memo=(
                f"## Portfolio\n"
                f"Total value is ${total:,.2f}, including ${portfolio.cash:,.2f} in "
                f"cash. Dividend income should add $4,280.00 next year."
            ),
            grounded=False,
            defect=Defect.INVENTED_FIGURE,
            note="Two correct figures followed by an invented one. This is the "
            "realistic case: the surrounding accuracy is what buys the bad claim "
            "its credibility.",
        ),
        MemoFixture(
            id="u10-plausible-precision",
            memo=(
                "## Spending\n"
                "Subscriptions cost $9,847.33 a year, about 35.2% of total spend."
            ),
            grounded=False,
            defect=Defect.INVENTED_FIGURE,
            note="Actual is $9,622.20 and no tool computed a share of spend. The "
            "two-decimal precision is what makes it read as computed.",
        ),
    ]

    return [*grounded, *ungrounded]


__all__ = [
    "FIXTURE_SOURCE_TEXT",
    "FIXTURE_SOURCE_URL",
    "Defect",
    "MemoFixture",
    "build_evidence_workspace",
    "build_fixtures",
]
