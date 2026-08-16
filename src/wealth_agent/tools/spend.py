"""Spend analytics for the `spend-analyst` subagent.

Two design decisions here carry most of the weight.

**The subagent never sees raw transactions.** Six months of card activity is
~285 rows. Handing those to a model costs thousands of tokens, invites it to do
arithmetic in its head, and produces totals nobody can check. Instead
:func:`load_spend_data` pulls the rows through MCP, normalizes them, caches them
to the run workspace, and returns *counts and date ranges*. Every question after
that is answered by a function that reads the cache and computes an exact
answer. The model orchestrates; it does not add.

**Every returned figure is computed, never estimated.** That is what makes the
numeric check in :mod:`wealth_agent.verify` meaningful: if a number appears in
the memo, some function here produced it, and the ledger recorded it.

The analytics are plain Python rather than pandas on purpose. At this data size
the performance is identical, there is one less dependency to break mid-demo,
and the code fits on a slide.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from typing import Any

from langchain_core.tools import BaseTool, tool

from wealth_agent.merchants import NON_SPEND_CATEGORIES, normalize
from wealth_agent.store import SPEND_DIR, RunWorkspace

CACHE_FILE = "transactions.json"

#: A charge repeating within this coefficient of variation is treated as a
#: fixed subscription rather than variable spend. 0.15 tolerates a plan change
#: or a tax tweak without swallowing genuinely variable merchants.
RECURRING_CV_THRESHOLD = 0.15


def _cache_path(ws: RunWorkspace):
    return ws.root / SPEND_DIR / CACHE_FILE


def _load(ws: RunWorkspace) -> list[dict[str, Any]]:
    path = _cache_path(ws)
    if not path.exists():
        msg = "No spend data loaded. Call load_spend_data first."
        raise ValueError(msg)
    return json.loads(path.read_text(encoding="utf-8"))


def _charges(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Only rows that represent money going out.

    Refunds and statement payments live in the same feed and are the reason
    `sum(amount)` gives the wrong answer. Excluding them here, once, means no
    downstream function has to remember to.
    """
    return [
        r
        for r in rows
        if r["type"] == "charge" and r["category"] not in NON_SPEND_CATEGORIES
    ]


def _in_window(row: dict[str, Any], start: str | None, end: str | None) -> bool:
    if start and row["date"] < start:
        return False
    return not (end and row["date"] > end)


def _r2(value: float) -> float:
    return round(value + 1e-9, 2)


def build_spend_tools(ws: RunWorkspace, get_transactions: Any) -> list[BaseTool]:
    """Build the spend toolset bound to one run.

    Args:
        ws: The run workspace; the cache and ledger live here.
        get_transactions: The MCP `get_card_transactions` tool.
    """

    @tool
    async def load_spend_data(
        start_date: str | None = None, end_date: str | None = None
    ) -> dict[str, Any]:
        """Load card transactions, normalize merchants and categories, and cache them.

        Call this once before any other spend tool. Returns a summary only —
        the rows themselves stay in the workspace so they never consume context.

        Args:
            start_date: ISO date, inclusive. Omit for all available history.
            end_date: ISO date, inclusive. Omit for all available history.
        """
        args: dict[str, Any] = {}
        if start_date:
            args["start_date"] = start_date
        if end_date:
            args["end_date"] = end_date
        raw = await get_transactions.ainvoke(args)
        payload = _parse_mcp_json(raw)

        rows = []
        for row in payload.get("transactions", []):
            merchant, category = normalize(row["description"])
            rows.append({**row, "merchant": merchant, "category": category})
        rows.sort(key=lambda r: (r["date"], r["id"]))
        _cache_path(ws).write_text(json.dumps(rows, indent=2), encoding="utf-8")

        charges = _charges(rows)
        uncategorized = sum(1 for r in charges if r["category"] == "Uncategorized")
        return {
            "transactions_loaded": len(rows),
            "charges": len(charges),
            "non_spend_rows": len(rows) - len(charges),
            "uncategorized_charges": uncategorized,
            "date_range": [rows[0]["date"], rows[-1]["date"]] if rows else [],
            "categories": sorted({r["category"] for r in charges}),
            "cached_at": f"/{SPEND_DIR}/{CACHE_FILE}",
        }

    @tool
    def spending_by_category(
        start_date: str | None = None, end_date: str | None = None
    ) -> dict[str, Any]:
        """Total spend per category, largest first. Excludes refunds and payments.

        Args:
            start_date: ISO date, inclusive.
            end_date: ISO date, inclusive.
        """
        rows = [r for r in _charges(_load(ws)) if _in_window(r, start_date, end_date)]
        totals: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            totals[row["category"]].append(row["amount"])
        grand = _r2(sum(sum(v) for v in totals.values()))
        breakdown = [
            {
                "category": cat,
                "total": _r2(sum(amounts)),
                "count": len(amounts),
                "percent_of_spend": _r2(100 * sum(amounts) / grand) if grand else 0.0,
            }
            for cat, amounts in totals.items()
        ]
        breakdown.sort(key=lambda d: -d["total"])
        return {"total_spend": grand, "transaction_count": len(rows), "by_category": breakdown}

    @tool
    def spending_by_merchant(
        top_n: int = 10,
        category: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """Top merchants by total spend.

        Args:
            top_n: How many merchants to return.
            category: Restrict to one category.
            start_date: ISO date, inclusive.
            end_date: ISO date, inclusive.
        """
        rows = [
            r
            for r in _charges(_load(ws))
            if _in_window(r, start_date, end_date)
            and (category is None or r["category"] == category)
        ]
        totals: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            totals[row["merchant"]].append(row["amount"])
        merchants = [
            {
                "merchant": name,
                "total": _r2(sum(amounts)),
                "count": len(amounts),
                "average": _r2(sum(amounts) / len(amounts)),
            }
            for name, amounts in totals.items()
        ]
        merchants.sort(key=lambda d: -d["total"])
        return {"merchants": merchants[:top_n], "merchants_found": len(merchants)}

    @tool
    def monthly_trend(category: str | None = None) -> dict[str, Any]:
        """Spend per calendar month, oldest first.

        The final month is flagged `partial` when the data ends mid-month —
        comparing a partial month against full ones is the most common way to
        report a fake decline.

        Args:
            category: Restrict to one category.
        """
        rows = _charges(_load(ws))
        if category:
            rows = [r for r in rows if r["category"] == category]
        if not rows:
            return {"months": [], "note": "no matching transactions"}
        buckets: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            buckets[row["date"][:7]].append(row["amount"])
        last_date = max(r["date"] for r in _load(ws))
        months = [
            {
                "month": month,
                "total": _r2(sum(amounts)),
                "count": len(amounts),
                "partial": month == last_date[:7],
            }
            for month, amounts in sorted(buckets.items())
        ]
        return {"months": months, "data_through": last_date}

    @tool
    def find_recurring_charges(min_occurrences: int = 3) -> dict[str, Any]:
        """Detect subscriptions: same merchant, stable amount, distinct months.

        Stability is measured by coefficient of variation (stdev / mean) below
        0.15. A merchant charged monthly at a wobbling amount is variable
        spend, not a subscription.

        Args:
            min_occurrences: Minimum distinct months required.
        """
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in _charges(_load(ws)):
            groups[row["merchant"]].append(row)

        found = []
        for merchant, rows in groups.items():
            months = {r["date"][:7] for r in rows}
            if len(months) < min_occurrences:
                continue
            amounts = [r["amount"] for r in rows]
            mean = statistics.fmean(amounts)
            if mean <= 0:
                continue
            cv = (statistics.stdev(amounts) / mean) if len(amounts) > 1 else 0.0
            if cv > RECURRING_CV_THRESHOLD:
                continue
            found.append(
                {
                    "merchant": merchant,
                    "category": rows[0]["category"],
                    "typical_amount": _r2(mean),
                    "months_observed": len(months),
                    "coefficient_of_variation": round(cv, 4),
                    "estimated_annual_cost": _r2(mean * 12),
                }
            )
        found.sort(key=lambda d: -d["estimated_annual_cost"])
        return {
            "recurring_charges": found,
            "total_estimated_annual_cost": _r2(
                sum(f["estimated_annual_cost"] for f in found)
            ),
        }

    @tool
    def largest_transactions(
        n: int = 10, start_date: str | None = None, end_date: str | None = None
    ) -> dict[str, Any]:
        """The n largest individual charges.

        Args:
            n: How many to return.
            start_date: ISO date, inclusive.
            end_date: ISO date, inclusive.
        """
        rows = [r for r in _charges(_load(ws)) if _in_window(r, start_date, end_date)]
        rows.sort(key=lambda r: -r["amount"])
        return {
            "transactions": [
                {
                    "date": r["date"],
                    "merchant": r["merchant"],
                    "category": r["category"],
                    "amount": r["amount"],
                }
                for r in rows[:n]
            ]
        }

    @tool
    def summarize_period(start_date: str, end_date: str) -> dict[str, Any]:
        """Spend summary for a window, compared against the preceding window
        of equal length.

        Args:
            start_date: ISO date, inclusive.
            end_date: ISO date, inclusive.
        """
        from datetime import date, timedelta

        rows = _charges(_load(ws))
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
        span = (end - start).days + 1
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=span - 1)

        current = [r for r in rows if _in_window(r, start_date, end_date)]
        prior = [
            r
            for r in rows
            if _in_window(r, prev_start.isoformat(), prev_end.isoformat())
        ]
        cur_total = _r2(sum(r["amount"] for r in current))
        prior_total = _r2(sum(r["amount"] for r in prior))

        cats: dict[str, float] = defaultdict(float)
        for row in current:
            cats[row["category"]] += row["amount"]
        top_categories = sorted(
            ({"category": c, "total": _r2(t)} for c, t in cats.items()),
            key=lambda d: -d["total"],
        )[:5]

        return {
            "period": [start_date, end_date],
            "total_spend": cur_total,
            "transaction_count": len(current),
            "prior_period": [prev_start.isoformat(), prev_end.isoformat()],
            "prior_period_spend": prior_total,
            # Both the absolute and the relative change are returned. An early
            # version returned only the percentage, and the agent — correctly
            # wanting to report the dollar delta — computed it in its head. The
            # verifier flagged that figure as unsupported, which was the right
            # call and the wrong fix to reach for. Scolding the model does not
            # work; giving it the number does.
            #
            # Generalized: when verification flags a figure, ask first whether
            # some tool should have returned it. A steady trickle of
            # `unsupported` findings is usually an API gap, not disobedience.
            "change_absolute": _r2(cur_total - prior_total),
            "change_percent": (
                _r2(100 * (cur_total - prior_total) / prior_total) if prior_total else None
            ),
            "top_categories": top_categories,
        }

    return [
        load_spend_data,
        spending_by_category,
        spending_by_merchant,
        monthly_trend,
        find_recurring_charges,
        largest_transactions,
        summarize_period,
    ]


def _parse_mcp_json(raw: Any) -> dict[str, Any]:
    """Decode an MCP tool result into a dict.

    MCP returns content blocks, and `langchain-mcp-adapters` surfaces them as a
    list of ``{"type": "text", "text": "<json>"}``. Tolerating every shape here
    keeps the callers from each growing their own unwrapping logic.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    if isinstance(raw, list):
        for block in raw:
            if isinstance(block, dict) and block.get("type") == "text":
                return json.loads(block["text"])
            if isinstance(block, str):
                return json.loads(block)
    msg = f"Unexpected MCP result shape: {type(raw)!r}"
    raise TypeError(msg)


__all__ = ["RECURRING_CV_THRESHOLD", "build_spend_tools"]
