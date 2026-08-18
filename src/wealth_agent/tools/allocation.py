"""Where the recommendation arithmetic lives.

This module is the answer to "why does this need to be deterministic?" A model
asked to rebalance a portfolio will produce plausible dollar amounts. They will
be close. They will not tie out, they will not respect the cash reserve, and
they will be different next Tuesday.

So the split is: **Python decides how much, the model decides whether and
explains why.** Every figure in a recommendation traces to a function here,
which means the grounding ledger records it and `verify.py` can check it — the
same machinery that catches a fabricated market claim catches a fabricated
trade size, with no extra work.

The functions deliberately mirror the shape of `portfolio.py`: each one names
its denominator in the payload, because "34.71%" means two different things
depending on whether cash is in the divisor, and a memo that does not say which
is not checkable.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from langchain_core.tools import BaseTool, tool

from wealth_agent.data.store import PORTFOLIO_DIR, SPEND_DIR, RunWorkspace
from wealth_agent.policy import Policy, load_policy
from wealth_agent.tools.portfolio import BALANCES_FILE, POSITIONS_FILE

#: Sectors whose target the portfolio can be under while holding nothing at
#: all. Buying into one needs an instrument name, which comes from the policy's
#: `preferred_instruments` rather than from the model picking a ticker.
_UNHELD = "no current holding"


def _r2(value: float) -> float:
    return round(value + 1e-9, 2)


def build_allocation_tools(ws: RunWorkspace, policy: Policy | None = None) -> list[BaseTool]:
    """Build the allocation toolset bound to one run and one policy."""
    pol = policy or load_policy()

    def _positions() -> list[dict[str, Any]]:
        path = ws.root / PORTFOLIO_DIR / POSITIONS_FILE
        if not path.exists():
            msg = "No portfolio loaded. The portfolio analyst must run load_portfolio first."
            raise ValueError(msg)
        return json.loads(path.read_text(encoding="utf-8"))

    def _balances() -> dict[str, Any]:
        path = ws.root / PORTFOLIO_DIR / BALANCES_FILE
        if not path.exists():
            msg = "No portfolio loaded. The portfolio analyst must run load_portfolio first."
            raise ValueError(msg)
        return json.loads(path.read_text(encoding="utf-8"))

    def _monthly_spend() -> tuple[float, int]:
        """Average full-month spend, and how many full months it averages over.

        Partial months are excluded rather than annualized. A reserve sized off
        a half-month of data is worse than no reserve, because it looks precise.
        """
        path = ws.root / SPEND_DIR / "transactions.json"
        if not path.exists():
            return 0.0, 0
        rows = json.loads(path.read_text(encoding="utf-8"))
        by_month: dict[str, float] = defaultdict(float)
        for row in rows:
            if row.get("is_payment") or row.get("amount", 0) <= 0:
                continue
            by_month[str(row.get("date", ""))[:7]] += float(row["amount"])
        if len(by_month) <= 1:
            return 0.0, 0
        # The newest month is almost always partial; drop it.
        full = [v for k, v in sorted(by_month.items())][:-1]
        return (_r2(sum(full) / len(full)), len(full)) if full else (0.0, 0)

    @tool
    def policy_targets() -> dict[str, Any]:
        """The investment policy this portfolio is measured against.

        Returns target weights, drift bands, the single-name cap, the cash
        reserve rule, and the minimum trade size. Read this before recommending
        anything — every recommendation must name the rule that triggered it.
        """
        return pol.to_json()

    @tool
    def drift_report() -> dict[str, Any]:
        """How far each sector and asset class sits from its policy target.

        Sector drift is measured against **equity only**; asset-class drift and
        the single-name cap are measured against **total value including
        cash**. Both denominators are stated in the result.

        A sector is `breached` when it is outside the policy's drift band.
        """
        rows, balances = _positions(), _balances()
        equity = sum(r["market_value"] for r in rows)
        total = balances["total_value"]
        cash = balances["cash"]

        held: dict[str, float] = defaultdict(float)
        for row in rows:
            held[row.get("sector", "Unknown")] += row["market_value"]

        sectors = []
        for sector in sorted(set(held) | set(pol.sector_targets)):
            value = held.get(sector, 0.0)
            current = _r2(100 * value / equity) if equity else 0.0
            target = pol.target_for(sector)
            gap = _r2(current - target)
            sectors.append(
                {
                    "sector": sector,
                    "market_value": _r2(value),
                    "current_percent_of_equity": current,
                    "target_percent_of_equity": target,
                    "drift_percentage_points": gap,
                    "dollars_from_target": _r2(value - equity * target / 100),
                    "breached": abs(gap) > pol.drift_band,
                    "status": _UNHELD if value == 0 else "held",
                }
            )
        sectors.sort(key=lambda d: -abs(d["drift_percentage_points"]))

        classes = []
        for name, value in (("equity", equity), ("cash", cash)):
            current = _r2(100 * value / total) if total else 0.0
            target = pol.asset_class_targets.get(name, 0.0)
            classes.append(
                {
                    "asset_class": name,
                    "market_value": _r2(value),
                    "current_percent_of_total": current,
                    "target_percent_of_total": target,
                    "drift_percentage_points": _r2(current - target),
                }
            )

        oversized = [
            {
                "symbol": r["symbol"],
                "market_value": r["market_value"],
                "percent_of_total": _r2(100 * r["market_value"] / total),
                "cap_percent": pol.max_single_name,
                "dollars_over_cap": _r2(r["market_value"] - total * pol.max_single_name / 100),
            }
            for r in rows
            if total and 100 * r["market_value"] / total > pol.max_single_name
        ]
        oversized.sort(key=lambda d: -d["percent_of_total"])

        return {
            "policy": pol.name,
            "sector_denominator": "equity_only",
            "asset_class_denominator": "total_value_including_cash",
            "equity_value": _r2(equity),
            "total_value": _r2(total),
            "drift_band_percentage_points": pol.drift_band,
            "sectors": sectors,
            "asset_classes": classes,
            "positions_over_single_name_cap": oversized,
        }

    @tool
    def cash_runway() -> dict[str, Any]:
        """How much cash is committed to the emergency reserve, and how much is free.

        The reserve is `cash_reserve_months` times average **full-month**
        spending, so it moves with what this person actually spends rather than
        a number someone picked once. Deployable cash is whatever is left, and
        it is frequently negative — which is itself the recommendation.
        """
        balances = _balances()
        cash = balances["cash"]
        monthly, months_observed = _monthly_spend()
        if months_observed == 0:
            return {
                "cash": cash,
                "reserve_required": None,
                "deployable_cash": None,
                "note": (
                    "No full month of spending data, so the reserve cannot be sized. "
                    "Treat all cash as committed until it can be."
                ),
            }
        required = _r2(monthly * pol.cash_reserve_months)
        return {
            "cash": cash,
            "average_full_month_spend": monthly,
            "full_months_observed": months_observed,
            "reserve_months": pol.cash_reserve_months,
            "reserve_required": required,
            "deployable_cash": _r2(cash - required),
            "months_of_cover_at_current_cash": _r2(cash / monthly) if monthly else None,
        }

    @tool
    def rebalance_plan(new_cash: float = 0.0) -> dict[str, Any]:
        """The specific trades that bring every breached sector back inside its band.

        This is the only place trade sizes come from. Copy the `dollars` field
        exactly into any recommendation — do not round it, adjust it, or net two
        rows together.

        The plan is **self-funding**: buys are paid for out of sell proceeds plus
        whatever cash is genuinely deployable after the reserve, and if that is
        not enough the buys are scaled down proportionally rather than the plan
        quietly assuming money that is not there.

        Args:
            new_cash: Money being added to the account. Defaults to zero.
        """
        drift = drift_report.invoke({})
        runway = cash_runway.invoke({})
        equity = drift["equity_value"]

        deployable = runway.get("deployable_cash")
        available = max(0.0, (deployable if deployable is not None else 0.0)) + max(0.0, new_cash)

        sells: list[dict[str, Any]] = []
        buys: list[dict[str, Any]] = []
        for row in drift["sectors"]:
            if not row["breached"]:
                continue
            amount = abs(row["dollars_from_target"])
            if amount < pol.min_trade_usd:
                continue
            side = "TRIM" if row["drift_percentage_points"] > 0 else "BUY"
            (sells if side == "TRIM" else buys).append(
                {
                    "action": side,
                    "sector": row["sector"],
                    "dollars": _r2(amount),
                    "reason_code": "SECTOR_OVER_BAND" if side == "TRIM" else "SECTOR_UNDER_BAND",
                    "detail": (
                        f"{row['sector']} is {row['current_percent_of_equity']}% of equity "
                        f"against a {row['target_percent_of_equity']}% target "
                        f"(band {pol.drift_band} points)"
                    ),
                }
            )

        # A sell has to name instruments. Take from the largest holding in the
        # breached sector first and spill into the next, capped at each
        # position's value — a single trim that ate 80% of one holding would be
        # arithmetically correct and nobody would execute it.
        rows = _positions()
        trimmed_by_symbol: dict[str, float] = defaultdict(float)
        for sell in sells:
            in_sector = sorted(
                (r for r in rows if r.get("sector") == sell["sector"]),
                key=lambda r: -r["market_value"],
            )
            remaining, legs = sell["dollars"], []
            for row in in_sector:
                if remaining < 0.01:
                    break
                take = min(remaining, row["market_value"])
                legs.append(
                    {
                        "symbol": row["symbol"],
                        "dollars": _r2(take),
                        "shares_est": _r2(take / row["last_price"]),
                    }
                )
                trimmed_by_symbol[row["symbol"]] += take
                remaining -= take
            sell["legs"] = legs
            sell["symbol"] = legs[0]["symbol"] if len(legs) == 1 else None
        for buy in buys:
            buy["symbol"] = pol.preferred_instruments.get(buy["sector"])

        # Single-name breaches the sector trims did not already resolve.
        #
        # These are a *risk* limit, not an allocation preference, so they are
        # emitted even when the position sits in a sector that is under target
        # — and when that happens the two policy rules genuinely disagree. The
        # plan surfaces the conflict rather than silently picking a winner,
        # because which rule wins is the client's decision, not the agent's.
        under_target = {
            r["sector"] for r in drift["sectors"] if r["drift_percentage_points"] < 0
        }
        for over in drift["positions_over_single_name_cap"]:
            excess = _r2(over["dollars_over_cap"] - trimmed_by_symbol.get(over["symbol"], 0.0))
            if excess < pol.min_trade_usd:
                continue
            row = next(r for r in rows if r["symbol"] == over["symbol"])
            action = {
                "action": "TRIM",
                "sector": row.get("sector", "Unknown"),
                "symbol": over["symbol"],
                "dollars": excess,
                "shares_est": _r2(excess / row["last_price"]),
                "reason_code": "SINGLE_NAME_OVER_CAP",
                "detail": (
                    f"{over['symbol']} is {over['percent_of_total']}% of total portfolio "
                    f"value against a {pol.max_single_name}% cap"
                ),
            }
            if row.get("sector") in under_target:
                action["policy_conflict"] = (
                    f"{row.get('sector')} is below its target weight, so this trim widens a "
                    f"sector gap in order to close a single-name risk breach. Replacing the "
                    f"exposure with a different holding in the same sector satisfies both."
                )
            sells.append(action)

        proceeds = sum(s["dollars"] for s in sells)  # includes single-name trims
        funding = _r2(proceeds + available)
        wanted = sum(b["dollars"] for b in buys)
        scaled = False
        if wanted > funding and wanted > 0:
            # Scale rather than drop: a partial move toward the target is
            # better than no move, and silently assuming money that does not
            # exist is how a plan becomes a fiction.
            factor = funding / wanted
            for buy in buys:
                buy["dollars"] = _r2(buy["dollars"] * factor)
                buy["scaled_to_available_funding"] = True
            scaled = True
            buys = [b for b in buys if b["dollars"] >= pol.min_trade_usd]

        holds = [
            {
                "action": "HOLD",
                "sector": row["sector"],
                "dollars": 0.0,
                "reason_code": "BELOW_MIN_TRADE",
                "detail": (
                    f"{row['sector']} is {row['drift_percentage_points']} points from target, "
                    f"a ${abs(row['dollars_from_target']):,.2f} gap — below the "
                    f"${pol.min_trade_usd:,.0f} minimum trade size"
                ),
            }
            for row in drift["sectors"]
            if row["breached"] and abs(row["dollars_from_target"]) < pol.min_trade_usd
        ]

        # Where the money that is not being reinvested actually goes. A plan
        # that sells $30k and buys $10k without saying what happens to the
        # other $20k is not a plan; the residual is a decision someone has to
        # make and it belongs on the page.
        spent = sum(b["dollars"] for b in buys)
        residual = _r2(proceeds - spent)
        shortfall = _r2(max(0.0, -(deployable if deployable is not None else 0.0)))
        to_reserve = _r2(min(max(0.0, residual), shortfall))
        uninvested = _r2(max(0.0, residual - to_reserve))

        return {
            "policy": pol.name,
            "equity_value": equity,
            "new_cash": _r2(new_cash),
            "deployable_cash": deployable,
            "sell_proceeds": _r2(proceeds),
            "total_funding_available": funding,
            "buys_scaled_to_funding": scaled,
            "actions": [*sells, *buys, *holds],
            "residual": {
                "proceeds_not_reinvested": residual,
                "applied_to_cash_reserve_shortfall": to_reserve,
                "left_uninvested": uninvested,
                "note": (
                    "Sector targets are shares of equity, so trimming to fix a single-name "
                    "breach shrinks equity and does not create a buy on its own. This "
                    "residual first closes the cash reserve shortfall; anything beyond that "
                    "is uninvested and needs a decision the policy does not make for you."
                ),
            },
            "unfunded": _r2(max(0.0, wanted - funding)),
            "caveats": [
                "No tool in this run returns tax lots. Every TRIM realizes a gain or "
                "loss whose tax consequence is not computed here.",
                "Share counts are estimates from the last traded price and will move "
                "before an order fills.",
            ],
        }

    return [policy_targets, drift_report, cash_runway, rebalance_plan]


__all__ = ["build_allocation_tools"]
