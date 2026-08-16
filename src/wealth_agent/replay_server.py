"""A local MCP server that mirrors the Robinhood tool surface over fixtures.

This is a real MCP server, not a mock. It speaks the protocol over stdio, and
``MultiServerMCPClient`` loads tools from it exactly as it would from
``agent.robinhood.com``. The agent code, the tool schemas, and the LangSmith
span tree are therefore identical in demo mode and live mode — which is the
whole point. A mock that short-circuits the transport would let a transport bug
hide until the moment you switch to real credentials on stage.

Run it directly to inspect the surface::

    uv run python -m wealth_agent.replay_server --server robinhood_trading --list

Tool names and shapes here are modelled on Robinhood's documented agentic
capabilities. Run ``wealth mcp probe`` against the live servers to capture the
authoritative schemas, then reconcile.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from typing import Any

from fastmcp import FastMCP

from wealth_agent import synthetic as syn
from wealth_agent.mcp_clients import BANKING, TRADING

_PORTFOLIO = syn.build_portfolio()
_TRANSACTIONS = syn.build_transactions()
_ORDERS = syn.build_orders(_PORTFOLIO)


# --------------------------------------------------------------------------
# Trading server
# --------------------------------------------------------------------------


def build_trading_server() -> FastMCP:
    mcp = FastMCP("robinhood-trading-replay")

    @mcp.tool
    def get_accounts() -> dict[str, Any]:
        """List the Robinhood accounts this agent can see, including the
        dedicated Agentic account it is allowed to trade in."""
        return {
            "accounts": [
                {
                    "account_number": syn.ACCOUNT_NUMBER,
                    "type": "individual",
                    "agentic": False,
                    "tradable_by_agent": False,
                },
                {
                    "account_number": syn.AGENTIC_ACCOUNT_NUMBER,
                    "type": "agentic",
                    "agentic": True,
                    "tradable_by_agent": True,
                },
            ]
        }

    @mcp.tool
    def get_account_balances(account_number: str = syn.ACCOUNT_NUMBER) -> dict[str, Any]:
        """Cash, equity, and total value for one account."""
        return {
            "account_number": account_number,
            "cash": _PORTFOLIO.cash,
            "equity_value": _PORTFOLIO.equity_value,
            "total_value": _PORTFOLIO.total_value,
            "total_cost_basis": _PORTFOLIO.total_cost_basis,
            "total_unrealized_pl": _PORTFOLIO.total_unrealized_pl,
            "as_of": _PORTFOLIO.as_of.isoformat(),
        }

    @mcp.tool
    def get_positions(account_number: str = syn.ACCOUNT_NUMBER) -> dict[str, Any]:
        """Every open equity position, with quantity, cost basis, market value
        and unrealized P/L."""
        return {
            "account_number": account_number,
            "as_of": _PORTFOLIO.as_of.isoformat(),
            "positions": [p.to_json() for p in _PORTFOLIO.positions],
        }

    @mcp.tool
    def get_orders(
        account_number: str = syn.ACCOUNT_NUMBER, limit: int = 25
    ) -> dict[str, Any]:
        """Recent orders, most recent first."""
        return {"account_number": account_number, "orders": _ORDERS[:limit]}

    @mcp.tool
    def get_quote(symbol: str) -> dict[str, Any]:
        """Current quote for one equity symbol."""
        return syn.quote_for(symbol, _PORTFOLIO)

    @mcp.tool
    def get_quotes(symbols: list[str]) -> dict[str, Any]:
        """Current quotes for several equity symbols at once."""
        return {"quotes": [syn.quote_for(s, _PORTFOLIO) for s in symbols]}

    @mcp.tool
    def get_watchlists() -> dict[str, Any]:
        """Named watchlists and the symbols on them."""
        return {
            "watchlists": [
                {
                    "name": "Semis",
                    "symbols": ["NVDA", "AVGO", "AMD", "TSM"],
                },
                {
                    "name": "Dividend",
                    "symbols": ["JPM", "XOM", "COST"],
                },
            ]
        }

    @mcp.tool
    def place_order(
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "market",
        limit_price: float | None = None,
        account_number: str = syn.AGENTIC_ACCOUNT_NUMBER,
    ) -> dict[str, Any]:
        """Place an equity order in the Agentic account.

        This is a state-changing tool. In demo mode it never reaches a broker —
        it echoes back the order it would have placed so the human-in-the-loop
        approval gate can be demonstrated without moving money.
        """
        quote = syn.quote_for(symbol, _PORTFOLIO)
        price = limit_price or quote.get("last_price", 0.0)
        return {
            "simulated": True,
            "account_number": account_number,
            "symbol": symbol.upper(),
            "side": side,
            "quantity": quantity,
            "order_type": order_type,
            "estimated_price": price,
            "estimated_total": round(quantity * price, 2),
            "state": "would_submit",
        }

    return mcp


# --------------------------------------------------------------------------
# Banking server
# --------------------------------------------------------------------------


def _in_range(row: dict[str, Any], start: str | None, end: str | None) -> bool:
    if start and row["date"] < start:
        return False
    return not (end and row["date"] > end)


def build_banking_server() -> FastMCP:
    mcp = FastMCP("robinhood-banking-replay")

    @mcp.tool
    def get_agentic_card() -> dict[str, Any]:
        """Details and limits of the agentic virtual card."""
        return {
            "card_id": "card_8841",
            "last_4": "4417",
            "status": "active",
            "monthly_limit": 5000.00,
            "per_transaction_limit": 500.00,
            "created_at": "2026-02-01",
        }

    @mcp.tool
    def get_card_transactions(
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """Transaction history for the agentic virtual card.

        Rows carry a `type` of `charge`, `refund`, or `payment`. Only `charge`
        rows are spend; the other two are why naive totals come out wrong.

        `description` is the raw network descriptor, exactly as a card feed
        delivers it — `SQ *BLUE BOTTLE COFFEE  OAKLAND`, not `Blue Bottle
        Coffee`. Normalizing and categorizing it is the client's job, which is
        why `wealth_agent.tools.spend` exists.
        """
        rows = [
            {k: v for k, v in r.items() if k not in ("merchant", "category")}
            for r in _TRANSACTIONS
            if _in_range(r, start_date, end_date)
        ]
        return {
            "count": len(rows[:limit]),
            "start_date": start_date or _TRANSACTIONS[0]["date"],
            "end_date": end_date or _TRANSACTIONS[-1]["date"],
            "transactions": rows[:limit],
        }

    @mcp.tool
    def get_card_settings() -> dict[str, Any]:
        """Spending controls currently configured on the agentic card."""
        return {
            "monthly_limit": 5000.00,
            "per_transaction_limit": 500.00,
            "blocked_categories": ["Gambling", "Crypto"],
            "require_approval_over": 250.00,
        }

    @mcp.tool
    def update_card_settings(
        monthly_limit: float | None = None,
        per_transaction_limit: float | None = None,
    ) -> dict[str, Any]:
        """Change the agentic card's spending controls.

        State-changing: gated behind human approval by the supervisor.
        """
        return {
            "simulated": True,
            "monthly_limit": monthly_limit or 5000.00,
            "per_transaction_limit": per_transaction_limit or 500.00,
            "state": "would_update",
        }

    return mcp


_BUILDERS = {TRADING: build_trading_server, BANKING: build_banking_server}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", choices=sorted(_BUILDERS), required=True)
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the tool surface as JSON and exit instead of serving.",
    )
    args = parser.parse_args()

    mcp = _BUILDERS[args.server]()
    if args.list:
        import asyncio

        tools = asyncio.run(mcp.list_tools())
        print(
            json.dumps(
                {
                    "server": args.server,
                    "generated": date.today().isoformat(),
                    "tools": [
                        {
                            "name": tool.name,
                            "description": (tool.description or "").strip(),
                            "input_schema": tool.parameters,
                        }
                        for tool in sorted(tools, key=lambda t: t.name)
                    ],
                },
                indent=2,
            )
        )
        return

    # The client spawns a fresh subprocess per tool call, so anything this
    # server writes to stderr is printed once per call and buries the agent's
    # own output during a demo.
    mcp.run(transport="stdio", show_banner=False, log_level="ERROR")


if __name__ == "__main__":
    main()
