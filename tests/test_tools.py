"""Analytics tools produce the numbers that end up in the memo.

Every assertion here is a number a human might act on. They are checked against
the synthetic ground truth rather than against themselves, so a change to the
generator that silently reshapes the demo fails loudly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wealth_agent import synthetic as syn
from wealth_agent.config import Settings
from wealth_agent.mcp_clients import BANKING, TRADING, build_client, load_tools
from wealth_agent.store import RunWorkspace
from wealth_agent.tools import build_portfolio_tools, build_spend_tools


@pytest.fixture
def workspace(tmp_path: Path) -> RunWorkspace:
    return RunWorkspace(run_id="tools", base=tmp_path)


@pytest.fixture
def settings() -> Settings:
    return Settings(demo_mode=True, allow_write_tools=False)


async def _spend_tools(ws: RunWorkspace, settings: Settings) -> dict:
    client = build_client(settings)
    banking = await load_tools(BANKING, settings=settings, client=client)
    by_name = {t.name: t for t in banking.all}
    tools = build_spend_tools(ws, by_name["get_card_transactions"])
    return {t.name: t for t in tools}


async def _portfolio_tools(ws: RunWorkspace, settings: Settings) -> dict:
    client = build_client(settings)
    trading = await load_tools(TRADING, settings=settings, client=client)
    by_name = {t.name: t for t in trading.all}
    tools = build_portfolio_tools(
        ws, by_name["get_positions"], by_name["get_account_balances"]
    )
    return {t.name: t for t in tools}


async def test_spend_tools_require_loading_first(
    workspace: RunWorkspace, settings: Settings
) -> None:
    tools = await _spend_tools(workspace, settings)
    with pytest.raises(ValueError, match="load_spend_data"):
        tools["spending_by_category"].invoke({})


async def test_load_spend_data_normalizes_and_caches(
    workspace: RunWorkspace, settings: Settings
) -> None:
    tools = await _spend_tools(workspace, settings)
    summary = await tools["load_spend_data"].ainvoke({})
    assert summary["transactions_loaded"] == len(syn.build_transactions())
    # Refunds and statement payments are not spend.
    assert summary["non_spend_rows"] > 0
    assert summary["charges"] + summary["non_spend_rows"] == summary["transactions_loaded"]
    # The rulebook covers this dataset completely, so nothing falls through.
    assert summary["uncategorized_charges"] == 0


async def test_category_totals_sum_to_the_grand_total(
    workspace: RunWorkspace, settings: Settings
) -> None:
    tools = await _spend_tools(workspace, settings)
    await tools["load_spend_data"].ainvoke({})
    result = tools["spending_by_category"].invoke({})
    parts = sum(row["total"] for row in result["by_category"])
    assert parts == pytest.approx(result["total_spend"], abs=0.02)
    assert sum(row["percent_of_spend"] for row in result["by_category"]) == pytest.approx(
        100.0, abs=0.1
    )


async def test_payments_are_excluded_from_spend(
    workspace: RunWorkspace, settings: Settings
) -> None:
    """The bug this guards: summing an amount column that mixes charges with
    statement payments produces a number that means nothing."""
    tools = await _spend_tools(workspace, settings)
    await tools["load_spend_data"].ainvoke({})
    categories = {
        row["category"] for row in tools["spending_by_category"].invoke({})["by_category"]
    }
    assert "Payment" not in categories


async def test_recurring_detection_finds_the_planted_subscriptions(
    workspace: RunWorkspace, settings: Settings
) -> None:
    tools = await _spend_tools(workspace, settings)
    await tools["load_spend_data"].ainvoke({})
    found = tools["find_recurring_charges"].invoke({"min_occurrences": 3})
    merchants = {row["merchant"] for row in found["recurring_charges"]}
    for expected in ("Netflix", "Spotify", "Equinox", "State Farm", "PG&E"):
        assert expected in merchants
    equinox = next(r for r in found["recurring_charges"] if r["merchant"] == "Equinox")
    assert equinox["typical_amount"] == pytest.approx(265.00, abs=0.01)
    assert equinox["estimated_annual_cost"] == pytest.approx(3180.00, abs=0.01)


async def test_variable_merchants_are_not_subscriptions(
    workspace: RunWorkspace, settings: Settings
) -> None:
    """A coffee shop visited monthly at varying amounts has a high coefficient
    of variation and must not be reported as a subscription."""
    tools = await _spend_tools(workspace, settings)
    await tools["load_spend_data"].ainvoke({})
    found = tools["find_recurring_charges"].invoke({"min_occurrences": 3})
    merchants = {row["merchant"] for row in found["recurring_charges"]}
    assert "Blue Bottle Coffee" not in merchants
    assert "Amazon" not in merchants


async def test_monthly_trend_flags_the_partial_month(
    workspace: RunWorkspace, settings: Settings
) -> None:
    """Comparing a partial month against full ones manufactures a trend."""
    tools = await _spend_tools(workspace, settings)
    await tools["load_spend_data"].ainvoke({})
    months = tools["monthly_trend"].invoke({})["months"]
    assert months[-1]["partial"] is True
    assert all(m["partial"] is False for m in months[:-1])


async def test_concentration_states_its_denominator(
    workspace: RunWorkspace, settings: Settings
) -> None:
    tools = await _portfolio_tools(workspace, settings)
    await tools["load_portfolio"].ainvoke({})
    result = tools["concentration"].invoke({})
    assert result["denominator"] == "total_value_including_cash"
    # Cash is in the denominator, so equity shares sum to less than 100.
    assert sum(p["percent_of_portfolio"] for p in result["positions"]) < 100


async def test_sector_exposure_uses_a_different_denominator(
    workspace: RunWorkspace, settings: Settings
) -> None:
    """The two denominators differing is exactly the trap fixture u06 plants."""
    tools = await _portfolio_tools(workspace, settings)
    await tools["load_portfolio"].ainvoke({})
    result = tools["sector_exposure"].invoke({})
    assert result["denominator"] == "equity_only"
    assert sum(s["percent_of_equity"] for s in result["sectors"]) == pytest.approx(
        100.0, abs=0.1
    )


async def test_portfolio_figures_match_the_synthetic_truth(
    workspace: RunWorkspace, settings: Settings
) -> None:
    tools = await _portfolio_tools(workspace, settings)
    summary = await tools["load_portfolio"].ainvoke({})
    portfolio = syn.build_portfolio()
    assert summary["total_value"] == pytest.approx(portfolio.total_value, abs=0.01)
    pl = tools["unrealized_pl_summary"].invoke({})
    assert pl["total_unrealized_pl"] == pytest.approx(portfolio.total_unrealized_pl, abs=0.02)


async def test_every_tool_result_reaches_the_ledger_via_the_agent(
    workspace: RunWorkspace, settings: Settings
) -> None:
    """Tools themselves do not write to the ledger — middleware does.

    Calling a tool directly must leave the ledger untouched, which is what
    makes the middleware the single point of truth rather than one of two.
    """
    tools = await _spend_tools(workspace, settings)
    await tools["load_spend_data"].ainvoke({})
    tool_entries = [e for e in workspace.ledger.entries() if e.kind == "tool_result"]
    assert tool_entries == []
