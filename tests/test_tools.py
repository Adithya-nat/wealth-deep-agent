"""Analytics tools produce the numbers that end up in the memo.

Every assertion here is a number a human might act on. They are checked against
the synthetic ground truth rather than against themselves, so a change to the
generator that silently reshapes the demo fails loudly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wealth_agent.data import synthetic as syn
from wealth_agent.config import Settings
from wealth_agent.mcp_servers.clients import BANKING, TRADING, build_client, load_tools
from wealth_agent.data.store import RunWorkspace
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


# --------------------------------------------------------------------------
# Upstream failures must not take the run with them
#
# A live banking server answered with newline-delimited JSON, the parser
# raised, and the exception unwound through the subagent and out of the
# supervisor — destroying a run that had already completed portfolio analysis
# and market research. Nothing was salvaged and nothing was reported but a
# traceback.
# --------------------------------------------------------------------------


class _BadServer:
    """An MCP tool that answers, but not with what was expected."""

    def __init__(self, text: str) -> None:
        self.text = text

    async def ainvoke(self, args):  # noqa: ANN001, ANN201
        return [{"type": "text", "text": self.text}]


@pytest.mark.parametrize(
    "body",
    [
        "<html><body>503 Service Unavailable</body></html>",
        "",
        "not json at all",
    ],
)
async def test_a_bad_spend_response_is_reported_not_raised(tmp_path, body: str) -> None:
    from wealth_agent.data.store import RunWorkspace
    from wealth_agent.tools.spend import build_spend_tools

    ws = RunWorkspace(run_id="degraded", base=tmp_path)
    load = build_spend_tools(ws, _BadServer(body))[0]
    result = await load.ainvoke({})
    assert "error" in result
    assert result["transactions_loaded"] == 0
    assert "do not estimate" in result["guidance"].lower()


async def test_a_bad_portfolio_response_is_reported_not_raised(tmp_path) -> None:
    from wealth_agent.data.store import RunWorkspace
    from wealth_agent.tools.portfolio import build_portfolio_tools

    ws = RunWorkspace(run_id="degraded", base=tmp_path)
    bad = _BadServer("<html>401</html>")
    load = build_portfolio_tools(ws, bad, bad)[0]
    result = await load.ainvoke({})
    assert "error" in result
    assert result["positions_loaded"] == 0


def test_newline_delimited_json_from_a_live_server_parses() -> None:
    """The exact shape that crashed a live run."""
    from wealth_agent.tools.spend import _parse_mcp_json

    ndjson = '{"id": "a", "amount": 12.5, "date": "2026-08-01"}\n{"id": "b", "amount": 7.0, "date": "2026-08-02"}'
    payload = _parse_mcp_json([{"type": "text", "text": ndjson}])
    assert len(payload["transactions"]) == 2
    assert payload["transactions"][1]["id"] == "b"


def test_a_paged_envelope_concatenates_its_rows() -> None:
    from wealth_agent.tools.spend import _parse_mcp_json

    paged = '{"transactions": [{"id": 1}], "next": "cursor"}\n{"transactions": [{"id": 2}]}'
    payload = _parse_mcp_json([{"type": "text", "text": paged}])
    assert [r["id"] for r in payload["transactions"]] == [1, 2]
    assert payload["next"] == "cursor"


def test_a_single_envelope_is_unchanged() -> None:
    """The fixture path must not regress while making the live path work."""
    from wealth_agent.tools.spend import _parse_mcp_json

    payload = _parse_mcp_json([{"type": "text", "text": '{"transactions": [{"id": 1}]}'}])
    assert payload == {"transactions": [{"id": 1}]}


def test_an_empty_response_is_a_failure_not_an_empty_result() -> None:
    """"Nothing came back" and "nothing happened" must not be confused.

    An empty body decoded as `{"transactions": []}` would put "you spent $0.00"
    in a memo — and it would verify perfectly, because zero is genuinely what
    the tool returned.
    """
    from wealth_agent.tools.spend import _parse_mcp_json

    with pytest.raises(ValueError, match="empty response"):
        _parse_mcp_json([{"type": "text", "text": "   "}])


def test_a_loss_grounds_whether_or_not_the_memo_writes_the_sign() -> None:
    """"UNH is down $1,621.90" is correct English the checker could not see.

    It extracts +1621.90 while the ledger holds -1621.90, so a true sentence was
    flagged unsupported on three separate runs. The fix is the repo's own rule:
    when verification flags a figure, ask first whether a tool should have
    returned it. Loosening the checker to match a positive against a negative
    was the alternative, and in a financial memo a sign error is the last thing
    a checker should learn to tolerate.
    """
    import json

    from wealth_agent.config import ARTIFACTS_DIR
    from wealth_agent.data.store import (
        RunWorkspace,
        extract_figures,
        grounded_values,
        is_grounded,
    )
    from wealth_agent.tools.portfolio import build_portfolio_tools

    ws = RunWorkspace(run_id="recommended", base=ARTIFACTS_DIR / "runs")
    tools = {t.name: t for t in build_portfolio_tools(ws, None, None)}
    summary = tools["unrealized_pl_summary"].invoke({})

    unh = next(r for r in summary["by_position"] if r["symbol"] == "UNH")
    assert unh["unrealized_pl"] < 0
    assert unh["unrealized_pl_abs"] == abs(unh["unrealized_pl"])

    grounded = grounded_values([json.dumps(summary)])
    for phrasing in ("UNH is down $1,621.90", "UNH is down -$1,621.90"):
        (figure,) = extract_figures(phrasing)
        assert is_grounded(figure.value, grounded), f"{phrasing!r} should ground"
