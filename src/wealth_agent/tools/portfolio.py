"""Portfolio analytics for the `portfolio-analyst` subagent.

Same contract as the spend tools: the MCP server returns raw positions, this
module caches them to the run workspace and answers questions with computed
numbers. Concentration and sector exposure in particular are exactly the
figures a model will happily estimate to one decimal place and get wrong, so
they get functions.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from langchain_core.tools import BaseTool, tool

from wealth_agent.store import PORTFOLIO_DIR, RunWorkspace
from wealth_agent.tools.spend import _parse_mcp_json

POSITIONS_FILE = "positions.json"
BALANCES_FILE = "balances.json"

#: A single position above this share of the portfolio is worth calling out.
#: Not a rule of finance — a reporting threshold, stated so the memo's
#: "concentrated" is defined rather than vibes.
CONCENTRATION_FLAG_PERCENT = 10.0


def _r2(value: float) -> float:
    return round(value + 1e-9, 2)


def build_portfolio_tools(
    ws: RunWorkspace, get_positions: Any, get_balances: Any
) -> list[BaseTool]:
    """Build the portfolio toolset bound to one run.

    Args:
        ws: The run workspace.
        get_positions: The MCP `get_positions` tool.
        get_balances: The MCP `get_account_balances` tool.
    """

    def _positions() -> list[dict[str, Any]]:
        path = ws.root / PORTFOLIO_DIR / POSITIONS_FILE
        if not path.exists():
            msg = "No portfolio loaded. Call load_portfolio first."
            raise ValueError(msg)
        return json.loads(path.read_text(encoding="utf-8"))

    def _balances() -> dict[str, Any]:
        path = ws.root / PORTFOLIO_DIR / BALANCES_FILE
        if not path.exists():
            msg = "No portfolio loaded. Call load_portfolio first."
            raise ValueError(msg)
        return json.loads(path.read_text(encoding="utf-8"))

    @tool
    async def load_portfolio(account_number: str | None = None) -> dict[str, Any]:
        """Load positions and balances for an account and cache them.

        Call this once before any other portfolio tool.

        Args:
            account_number: Defaults to the primary individual account.
        """
        args = {"account_number": account_number} if account_number else {}
        positions = _parse_mcp_json(await get_positions.ainvoke(args))
        balances = _parse_mcp_json(await get_balances.ainvoke(args))

        rows = positions.get("positions", [])
        (ws.root / PORTFOLIO_DIR / POSITIONS_FILE).write_text(
            json.dumps(rows, indent=2), encoding="utf-8"
        )
        (ws.root / PORTFOLIO_DIR / BALANCES_FILE).write_text(
            json.dumps(balances, indent=2), encoding="utf-8"
        )
        return {
            "positions_loaded": len(rows),
            "symbols": sorted(r["symbol"] for r in rows),
            "total_value": balances.get("total_value"),
            "cash": balances.get("cash"),
            "as_of": balances.get("as_of"),
            "cached_at": f"/{PORTFOLIO_DIR}/",
        }

    @tool
    def concentration() -> dict[str, Any]:
        """Each position's share of total portfolio value, largest first.

        Denominator is total value including cash, so the percentages sum to
        less than 100. Stating the denominator matters: the same portfolio is
        "23% VOO" or "27% VOO" depending on whether you count cash, and a memo
        that does not say which is not checkable.
        """
        rows, balances = _positions(), _balances()
        total = balances["total_value"]
        out = [
            {
                "symbol": r["symbol"],
                "market_value": r["market_value"],
                "percent_of_portfolio": _r2(100 * r["market_value"] / total),
            }
            for r in rows
        ]
        out.sort(key=lambda d: -d["percent_of_portfolio"])
        flagged = [d for d in out if d["percent_of_portfolio"] >= CONCENTRATION_FLAG_PERCENT]
        return {
            "denominator": "total_value_including_cash",
            "total_value": total,
            "positions": out,
            "flag_threshold_percent": CONCENTRATION_FLAG_PERCENT,
            "flagged": flagged,
        }

    @tool
    def sector_exposure() -> dict[str, Any]:
        """Market value and share of equity by sector, largest first.

        Denominator here is equity only, not total value — a sector weight that
        included cash would understate every sector.
        """
        rows = _positions()
        equity = sum(r["market_value"] for r in rows)
        buckets: dict[str, float] = defaultdict(float)
        for row in rows:
            buckets[row.get("sector", "Unknown")] += row["market_value"]
        sectors = [
            {
                "sector": sector,
                "market_value": _r2(value),
                "percent_of_equity": _r2(100 * value / equity) if equity else 0.0,
            }
            for sector, value in buckets.items()
        ]
        sectors.sort(key=lambda d: -d["market_value"])
        return {
            "denominator": "equity_only",
            "equity_value": _r2(equity),
            "sectors": sectors,
        }

    @tool
    def unrealized_pl_summary() -> dict[str, Any]:
        """Unrealized gain/loss overall and per position, worst first."""
        rows = _positions()
        by_position = sorted(
            (
                {
                    "symbol": r["symbol"],
                    "unrealized_pl": r["unrealized_pl"],
                    "unrealized_pl_percent": r["unrealized_pl_percent"],
                    "cost_basis": r["cost_basis"],
                    "market_value": r["market_value"],
                }
                for r in rows
            ),
            key=lambda d: d["unrealized_pl"],
        )
        total_cost = sum(r["cost_basis"] for r in rows)
        total_value = sum(r["market_value"] for r in rows)
        return {
            "total_cost_basis": _r2(total_cost),
            "total_market_value": _r2(total_value),
            "total_unrealized_pl": _r2(total_value - total_cost),
            "total_unrealized_pl_percent": (
                _r2(100 * (total_value - total_cost) / total_cost) if total_cost else 0.0
            ),
            "losers": [d for d in by_position if d["unrealized_pl"] < 0],
            "by_position": by_position,
        }

    @tool
    def position_detail(symbol: str) -> dict[str, Any]:
        """Everything known about one holding.

        Args:
            symbol: Ticker, case-insensitive.
        """
        for row in _positions():
            if row["symbol"].upper() == symbol.upper():
                return row
        return {"error": f"no position in {symbol.upper()}"}

    return [
        load_portfolio,
        concentration,
        sector_exposure,
        unrealized_pl_summary,
        position_detail,
    ]


__all__ = ["CONCENTRATION_FLAG_PERCENT", "build_portfolio_tools"]
