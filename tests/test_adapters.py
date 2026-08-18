"""The live schema adapter.

Every fixture in this file is a real shape captured from Robinhood's servers,
trimmed to the fields that matter. The adapter is the one place where a number
gets *created* rather than passed through, so it is the one place where a bug
produces a plausible wrong figure instead of a crash.
"""

from __future__ import annotations

import pytest

from wealth_agent.data.adapters import (
    LiveAccountAdapter,
    adapt_card_transactions,
    as_float,
    company_name,
    current_price,
    resolve_account,
    resolve_capability_tools,
    unwrap,
)
from wealth_agent.mcp_servers.clients import CapabilityError

# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("305.980000", 305.98),
        ("1,234.50", 1234.5),
        ("$42", 42.0),
        (7, 7.0),
        (7.5, 7.5),
        (None, None),
        ("", None),
        ("n/a", None),
        # True is an int in Python and would otherwise parse as 1.0.
        (True, None),
    ],
)
def test_as_float(raw: object, expected: float | None) -> None:
    assert as_float(raw) == expected


def test_unparseable_is_none_not_zero() -> None:
    """Zero is a real balance. Conflating it with 'unreadable' fabricates a fact."""
    assert as_float("unavailable") is None
    assert as_float("0") == 0.0


def test_unwrap_strips_the_envelope_and_the_guide() -> None:
    payload = {"data": {"total_value": "100"}, "guide": "prose for a model"}
    assert unwrap(payload) == {"total_value": "100"}


# --------------------------------------------------------------------------
# Account selection
# --------------------------------------------------------------------------


def test_prefers_the_default_account() -> None:
    payload = {
        "data": {
            "accounts": [
                {"account_number": "AAA", "is_default": False},
                {"account_number": "BBB", "is_default": True},
            ]
        }
    }
    assert resolve_account(payload) == "BBB"


def test_skips_deactivated_accounts() -> None:
    payload = {
        "data": {
            "accounts": [
                {"account_number": "AAA", "deactivated": True, "is_default": True},
                {"account_number": "BBB"},
            ]
        }
    }
    assert resolve_account(payload) == "BBB"


def test_ambiguity_raises_rather_than_guessing() -> None:
    """Analyzing the wrong account produces a memo that is wrong about *whose*."""
    payload = {"data": {"accounts": [{"account_number": "AAA"}, {"account_number": "BBB"}]}}
    with pytest.raises(CapabilityError, match="none is marked default"):
        resolve_account(payload)


def test_no_active_account_raises() -> None:
    with pytest.raises(CapabilityError, match="No active brokerage account"):
        resolve_account({"data": {"accounts": [{"account_number": "A", "deactivated": True}]}})


# --------------------------------------------------------------------------
# Derived fields
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("Apple, Inc. engages in the design, manufacture, and sale of smartphones.",
         "Apple, Inc."),
        ("NVIDIA Corporation operates as a computing company.", "NVIDIA Corporation"),
        ("Acme Corp provides widgets to industry.", "Acme Corp"),
        # A legal name that genuinely starts with "The".
        ("The Coca-Cola Company engages in the beverage business.",
         "The Coca-Cola Company"),
        # No boundary verb: the ticker is better than a wrong guess.
        ("A company.", "TICK"),
        (None, "TICK"),
        ("", "TICK"),
    ],
)
def test_company_name(description: str | None, expected: str) -> None:
    assert company_name(description, "TICK") == expected


def test_the_earliest_boundary_wins_not_the_first_listed() -> None:
    """Captured live from GOOGL, and the reason this function was rewritten.

    Scanning boundaries in list order matched `engages in` and returned
    "Alphabet, Inc. is a holding company, which" — short enough to pass a length
    check and obviously not a name.
    """
    described = (
        "Alphabet, Inc. is a holding company, which engages in the business of "
        "acquisition and operation of different companies."
    )
    assert company_name(described, "GOOGL") == "Alphabet, Inc."


def test_an_etf_has_no_company_name() -> None:
    """A fund profile describes an objective. There is no name to recover."""
    described = (
        "The fund seeks to track the performance of a benchmark index that "
        "measures the investment return of the overall stock market."
    )
    assert company_name(described, "VTI") == "VTI"


@pytest.mark.parametrize(
    "described",
    [
        "x" * 80 + " engages in things",  # too long
        "One two three four five six engages in things",  # too many words
        "Something, which is a subordinate clause provides cover",  # prose marker
    ],
)
def test_company_name_rejects_prose(described: str) -> None:
    """A 'name' that reads like a sentence is a parse failure in a costume."""
    assert company_name(described, "TICK") == "TICK"


def test_current_price_takes_the_more_recent_trade() -> None:
    entry = {
        "quote": {
            "last_trade_price": "305.98",
            "venue_last_trade_time": "2026-08-14T19:59:59Z",
            "last_non_reg_trade_price": "307.06",
            "venue_last_non_reg_trade_time": "2026-08-17T09:38:26Z",
        }
    }
    price, stamp = current_price(entry)
    assert price == 307.06, "extended-hours trade is newer here"
    assert stamp.startswith("2026-08-17")


def test_current_price_can_prefer_the_regular_session() -> None:
    """Neither field is reliably newer, which is why the timestamps decide."""
    entry = {
        "quote": {
            "last_trade_price": "310.00",
            "venue_last_trade_time": "2026-08-17T16:00:00Z",
            "last_non_reg_trade_price": "307.06",
            "venue_last_non_reg_trade_time": "2026-08-17T09:38:26Z",
        }
    }
    assert current_price(entry)[0] == 310.00


def test_current_price_falls_back_to_the_official_close() -> None:
    entry = {"quote": {}, "close": {"price": "305.93", "date": "2026-08-14"}}
    assert current_price(entry) == (305.93, "2026-08-14")


# --------------------------------------------------------------------------
# The full snapshot
# --------------------------------------------------------------------------


class _FakeTool:
    def __init__(self, name: str, payload: object) -> None:
        self.name = name
        self._payload = payload
        self.calls: list[dict] = []

    async def ainvoke(self, args: dict) -> object:
        self.calls.append(args)
        return self._payload


def _live_tools() -> dict[str, _FakeTool]:
    return {
        "get_accounts": _FakeTool(
            "get_accounts",
            {"data": {"accounts": [{"account_number": "ACC1", "is_default": True}]}},
        ),
        "get_equity_positions": _FakeTool(
            "get_equity_positions",
            {
                "data": {
                    "positions": [
                        {"symbol": "AAPL", "quantity": "10.0000",
                         "average_buy_price": "100.0000"},
                        {"symbol": "NVDA", "quantity": "5.0000",
                         "average_buy_price": "200.0000"},
                        # A closed position stays in the feed at quantity zero.
                        {"symbol": "TSLA", "quantity": "0.0000",
                         "average_buy_price": "300.0000"},
                    ]
                }
            },
        ),
        "get_equity_quotes": _FakeTool(
            "get_equity_quotes",
            {
                "data": {
                    "results": [
                        {"quote": {"symbol": "AAPL", "last_trade_price": "150.00",
                                   "venue_last_trade_time": "2026-08-17T16:00:00Z"}},
                        {"quote": {"symbol": "NVDA", "last_trade_price": "250.00",
                                   "venue_last_trade_time": "2026-08-17T16:00:00Z"}},
                    ]
                }
            },
        ),
        "get_equity_fundamentals": _FakeTool(
            "get_equity_fundamentals",
            {
                "results": [
                    {"symbol": "AAPL", "sector": "Electronic Technology",
                     "description": "Apple, Inc. engages in the design of phones."},
                    {"symbol": "NVDA", "sector": "Electronic Technology",
                     "description": "NVIDIA Corporation operates as a computing company."},
                ]
            },
        ),
        "get_portfolio": _FakeTool(
            "get_portfolio",
            {"data": {"total_value": "5000.00", "equity_value": "2750.00",
                      "cash": "2250.00",
                      "buying_power": {"buying_power": "2250.00"}}},
        ),
    }


async def test_positions_are_enriched_and_valued() -> None:
    adapter = LiveAccountAdapter(_live_tools())
    result = await adapter.positions_tool().ainvoke({})
    rows = {r["symbol"]: r for r in result["positions"]}

    assert "TSLA" not in rows, "a closed position is not a holding"
    assert rows["AAPL"]["market_value"] == 1500.00  # 10 x 150
    assert rows["AAPL"]["cost_basis"] == 1000.00  # 10 x 100
    assert rows["AAPL"]["unrealized_pl"] == 500.00
    assert rows["AAPL"]["unrealized_pl_percent"] == 50.00
    assert rows["AAPL"]["name"] == "Apple, Inc."
    assert rows["AAPL"]["sector"] == "Electronic Technology"


async def test_positions_are_sorted_largest_first() -> None:
    adapter = LiveAccountAdapter(_live_tools())
    result = await adapter.positions_tool().ainvoke({})
    values = [r["market_value"] for r in result["positions"]]
    assert values == sorted(values, reverse=True)


async def test_balances_totals_are_computed_from_the_same_rows() -> None:
    """Two independent fetches is how positions stop adding up to the total."""
    adapter = LiveAccountAdapter(_live_tools())
    balances = await adapter.balances_tool().ainvoke({})

    assert balances["total_value"] == 5000.00
    assert balances["cash"] == 2250.00
    assert balances["total_cost_basis"] == 2000.00  # 1000 + 1000
    assert balances["total_unrealized_pl"] == 750.00  # 500 + 250
    assert balances["positions_priced"] == 2
    assert balances["account_number"] == "ACC1"
    assert balances["as_of"], "a portfolio total with no as-of time silently rots"


async def test_one_fetch_serves_both_tools() -> None:
    tools = _live_tools()
    adapter = LiveAccountAdapter(tools)
    await adapter.positions_tool().ainvoke({})
    await adapter.balances_tool().ainvoke({})
    assert len(tools["get_equity_positions"].calls) == 1
    assert len(tools["get_equity_quotes"].calls) == 1


async def test_an_unpriced_position_omits_its_derived_figures() -> None:
    """`market_value: 0.0` for an unpriced holding is a fabricated number."""
    tools = _live_tools()
    tools["get_equity_quotes"] = _FakeTool("get_equity_quotes", {"data": {"results": []}})
    adapter = LiveAccountAdapter(tools)
    rows = (await adapter.positions_tool().ainvoke({}))["positions"]

    for row in rows:
        assert "market_value" not in row
        assert "unrealized_pl" not in row
        assert row["cost_basis"] is not None, "cost basis needs no quote"

    balances = await adapter.balances_tool().ainvoke({})
    assert balances["positions_priced"] == 0
    assert balances["positions_total"] == 2


async def test_missing_fundamentals_degrade_to_ticker_and_unknown() -> None:
    tools = _live_tools()
    tools["get_equity_fundamentals"] = _FakeTool("get_equity_fundamentals", {"results": []})
    rows = {r["symbol"]: r for r in
            (await LiveAccountAdapter(tools).positions_tool().ainvoke({}))["positions"]}
    assert rows["AAPL"]["name"] == "AAPL"
    assert rows["AAPL"]["sector"] == "Unknown"


async def test_symbols_are_chunked_for_the_ten_symbol_limit() -> None:
    tools = _live_tools()
    tools["get_equity_positions"] = _FakeTool(
        "get_equity_positions",
        {"data": {"positions": [
            {"symbol": f"S{i:02d}", "quantity": "1", "average_buy_price": "1"}
            for i in range(23)
        ]}},
    )
    await LiveAccountAdapter(tools).positions_tool().ainvoke({})
    assert len(tools["get_equity_fundamentals"].calls) == 3, "23 symbols, max 10 each"
    assert len(tools["get_equity_quotes"].calls) == 2, "23 symbols, max 20 each"


# --------------------------------------------------------------------------
# Card transactions
# --------------------------------------------------------------------------


async def test_card_transactions_are_normalized() -> None:
    live = _FakeTool(
        "banking_get_agent_card_transactions",
        {"data": {"transactions": [
            {"transaction_id": "t1", "created_at": "2026-08-01T10:00:00Z",
             "settled_amount": "-42.50", "merchant_name": "ACME COFFEE #22"},
        ]}},
    )
    rows = (await adapt_card_transactions(live).ainvoke({}))["transactions"]
    assert rows[0]["id"] == "t1"
    assert rows[0]["date"] == "2026-08-01", "dates are trimmed to ISO days"
    assert rows[0]["amount"] == -42.50
    assert rows[0]["description"] == "ACME COFFEE #22"


async def test_an_empty_card_feed_is_a_valid_answer() -> None:
    """Most accounts have never used the agent virtual card."""
    live = _FakeTool("banking_get_agent_card_transactions", {"data": {"transactions": []}})
    assert (await adapt_card_transactions(live).ainvoke({}))["transactions"] == []


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------


def test_fixture_names_bypass_the_adapter_entirely() -> None:
    """Demo mode must stay byte-identical, and pay none of this cost."""
    fixtures = {n: _FakeTool(n, {}) for n in
                ("get_positions", "get_account_balances", "get_card_transactions")}
    resolved = resolve_capability_tools(fixtures)
    assert resolved["positions"] is fixtures["get_positions"]
    assert resolved["balances"] is fixtures["get_account_balances"]
    assert resolved["card_transactions"] is fixtures["get_card_transactions"]


def test_live_names_route_through_the_adapter() -> None:
    tools = {**_live_tools(),
             "banking_get_agent_card_transactions": _FakeTool("banking", {})}
    resolved = resolve_capability_tools(tools)
    assert resolved["positions"].name == "get_positions"
    assert resolved["balances"].name == "get_account_balances"
    assert resolved["card_transactions"].name == "get_card_transactions"


async def test_a_server_missing_a_needed_tool_says_which() -> None:
    accounts = {"data": {"accounts": [{"account_number": "ACC1", "is_default": True}]}}
    adapter = LiveAccountAdapter({"get_accounts": _FakeTool("get_accounts", accounts)})
    with pytest.raises(CapabilityError, match="get_equity_positions"):
        await adapter.positions_tool().ainvoke({})
