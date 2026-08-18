"""Make Robinhood's live responses answer to the contract the fixtures set.

The replay fixtures and the real servers describe the same account in different
words. Some of the difference is cosmetic — an envelope here, numbers as
strings there. Some of it is substantive: **live positions carry no market
value at all.** ``get_equity_positions`` returns what you own and what you paid;
what it is worth today lives in a different tool, and what industry it is in
lives in a third.

======================  ==========================================  ============
fixture field           live source                                 kind
======================  ==========================================  ============
symbol                  get_equity_positions                        direct
quantity                get_equity_positions (string)               parsed
average_cost            get_equity_positions.average_buy_price      renamed
last_price              get_equity_quotes                           **fetched**
sector                  get_equity_fundamentals                     **fetched**
name                    get_equity_fundamentals.description         **derived**
market_value            quantity x last_price                       **computed**
cost_basis              quantity x average_cost                     **computed**
unrealized_pl           market_value - cost_basis                   **computed**
total_value, cash       get_portfolio (nested, strings)             parsed
total_cost_basis        sum over positions                          **computed**
======================  ==========================================  ============

Two things follow from that table, and both are the point.

**The adapter is the right place for the arithmetic.** Every computed row above
is multiplication and subtraction over numbers a server returned. Doing it here
means it happens once, in Python, under test — rather than four times, in a
model's head, at whatever precision it feels like. "The LLM reasons,
deterministic code computes" is not a slogan you apply only to the analytics
layer; a schema adapter is where it gets tested first.

**Adapting is not free of judgment.** ``last_price`` requires choosing between
two prices with two timestamps; ``name`` has no live field at all and has to be
derived from prose or given up on. Those choices are made explicitly below,
with the losing option written down, because an adapter that quietly picks one
is indistinguishable from an adapter that picks wrong.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from wealth_agent.mcp_servers.clients import CAPABILITIES, CapabilityError

#: Robinhood wraps payloads in ``{"data": ..., "guide": "..."}``. ``guide`` is
#: prose written for a model, not data, and it must not reach the ledger as
#: though it were evidence.
ENVELOPE_KEY = "data"

#: ``get_equity_fundamentals`` accepts at most 10 symbols; ``get_equity_quotes``
#: drops the official closes above 20. Both are stated in the tool descriptions.
FUNDAMENTALS_CHUNK = 10
QUOTES_CHUNK = 20

#: Verbs a company profile uses to transition from its legal name into what it
#: does: "Apple, Inc. **engages in** the design...". Everything before the
#: *earliest* of these is the name. Deliberately a closed list — a general
#: "first sentence" rule returns a paragraph for any company whose profile is
#: phrased differently, and a wrong company name in a wealth memo is worse than
#: a bare ticker.
NAME_BOUNDARIES = (
    " engages in",
    " operates as",
    " provides ",
    " designs ",
    " manufactures ",
    " develops ",
    " is a ",
    " is an ",
    " is engaged in",
)

#: Words that only appear once prose has started. Any of these in the candidate
#: means a boundary matched too late and dragged a clause along with it.
#:
#: This is the guard that was missing. "Alphabet, Inc. is a holding company,
#: which engages in..." matched ``engages in`` and yielded *"Alphabet, Inc. is
#: a holding company, which"* — short enough to pass a length check, and
#: unmistakably not a name. Length alone cannot tell prose from a name; the
#: presence of a subordinate clause can.
PROSE_MARKERS = frozenset(
    {
        "which", "that", "whose", "seeks", "aims", "engages", "operates",
        "provides",
        # Fund-objective language. "The fund provides broad exposure..." yields
        # the candidate "The fund", which is short, clause-free, and still not a
        # name. An ETF has no company behind it, so the ticker is the honest
        # answer — and for the rare real company with "Trust" in its legal name,
        # falling back to its ticker is a cosmetic loss, never a wrong claim.
        "fund", "trust", "etf", "index", "portfolio",
    }
)

#: A name longer than this is prose that slipped through, not a company name.
MAX_NAME_CHARS = 60

#: "Taiwan Semiconductor Manufacturing Company Limited" is five words and real.
#: Six is prose.
MAX_NAME_WORDS = 5


def as_float(value: Any) -> float | None:
    """Coerce Robinhood's stringified decimals to floats.

    Returns ``None`` rather than ``0.0`` for anything unparseable. Zero is a
    real quantity and a real balance; conflating "none held" with "we could not
    read it" would put a fabricated zero into a memo.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip().replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None


def unwrap(payload: Any) -> Any:
    """Strip the ``data`` envelope and drop the model-facing ``guide`` prose."""
    if isinstance(payload, dict) and ENVELOPE_KEY in payload:
        return payload[ENVELOPE_KEY]
    return payload


def resolve_account(payload: Any) -> str:
    """Choose which brokerage account the memo is about.

    Prefers the account Robinhood marks default, skipping anything deactivated.
    Raises rather than guessing when several plausible accounts remain and none
    is default — silently analyzing the wrong account produces a memo that is
    correct about someone's money and wrong about *this* person's.
    """
    data = unwrap(payload)
    accounts = data.get("accounts", []) if isinstance(data, dict) else data
    live = [
        a
        for a in accounts or []
        if not a.get("deactivated") and not a.get("permanently_deactivated")
    ]
    if not live:
        msg = "No active brokerage account found in get_accounts."
        raise CapabilityError(msg)
    for account in live:
        if account.get("is_default"):
            return str(account["account_number"])
    if len(live) == 1:
        return str(live[0]["account_number"])
    numbers = ", ".join(str(a.get("account_number")) for a in live)
    msg = (
        f"{len(live)} active accounts and none is marked default ({numbers}). "
        f"Pass account_number explicitly to load_portfolio."
    )
    raise CapabilityError(msg)


def company_name(description: str | None, symbol: str) -> str:
    """Recover a company name from its profile text, or fall back to the ticker.

    The live API has no name field. The profile reliably opens with the legal
    name, so the name is whatever precedes the *earliest* descriptive verb — and
    if nothing matches, or the result reads like prose, the ticker is returned
    unchanged. A memo saying "NVDA" is merely terse; a memo confidently naming
    the wrong company is a defect.

    Scanning by earliest position rather than by list order is the whole trick.
    Ordering the boundaries by hand and taking the first that matches picks
    whichever verb the author happened to list first, not the one that actually
    ends the name — which is how "Alphabet, Inc." became "Alphabet, Inc. is a
    holding company, which".

    An ETF has no company at all: its profile describes a fund objective, every
    candidate trips a prose marker, and it correctly falls back to the ticker.
    """
    if not description:
        return symbol
    text = description.strip()
    hits = [(text.find(b), b) for b in NAME_BOUNDARIES if text.find(b) > 0]
    if not hits:
        return symbol
    index, _ = min(hits)
    candidate = re.sub(r"[,\s]+$", "", text[:index].strip())
    words = candidate.split()
    if not words or len(candidate) > MAX_NAME_CHARS or len(words) > MAX_NAME_WORDS:
        return symbol
    if {w.strip(",.").lower() for w in words} & PROSE_MARKERS:
        return symbol
    return candidate


def _timestamp(value: str | None) -> datetime:
    """Parse an RFC 3339 timestamp, treating anything unreadable as oldest."""
    if not value:
        return datetime.min.replace(tzinfo=UTC)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)


def current_price(entry: dict[str, Any]) -> tuple[float | None, str | None]:
    """Pick the current price from a quote, and report when it was struck.

    A quote carries a regular-session trade and an extended-hours trade, each
    with its own timestamp, and neither is reliably the newer one. Robinhood's
    own guidance is to take whichever traded most recently, so that is what this
    does; the official prior close is the fallback when nothing has traded.

    Returned with its timestamp so the caller can state *as of when* the
    valuation holds. A portfolio total with no as-of time is a number that
    silently rots.
    """
    quote = entry.get("quote") or {}
    candidates = [
        (as_float(quote.get("last_trade_price")), quote.get("venue_last_trade_time")),
        (
            as_float(quote.get("last_non_reg_trade_price")),
            quote.get("venue_last_non_reg_trade_time"),
        ),
    ]
    priced = [(p, t) for p, t in candidates if p is not None]
    if priced:
        price, stamp = max(priced, key=lambda pair: _timestamp(pair[1]))
        return price, stamp
    close = entry.get("close") or {}
    return as_float(close.get("price")), close.get("date")


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _rows(payload: Any, *keys: str) -> list[dict[str, Any]]:
    """Pull a list of rows out of a payload, trying each key in turn."""
    data = unwrap(payload)
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


class AdaptedTool:
    """A live tool wearing the name and response shape of a fixture tool.

    Deliberately duck-typed to the pieces the analytics layer uses — ``name``
    and ``ainvoke`` — rather than subclassing ``BaseTool``. These are never
    handed to a model and never appear in a tool schema; they exist to keep one
    code path behind ``load_portfolio``. Making them look like real tools to the
    agent would invite exactly the confusion this module exists to prevent.
    """

    def __init__(self, name: str, fn: Any) -> None:
        self.name = name
        self._fn = fn

    async def ainvoke(self, args: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._fn(args or {})


class LiveAccountAdapter:
    """Presents the live trading server as the fixture portfolio tools.

    Positions and balances are built from the same enriched snapshot, so asking
    for both costs one round of fetches rather than two — and, more importantly,
    guarantees the totals in ``get_account_balances`` are the totals *of* the
    rows in ``get_positions``. Computing them from two independent fetches is
    how a memo ends up with positions that do not add up to its own stated
    total.
    """

    def __init__(self, by_name: dict[str, Any]) -> None:
        self._tools = by_name
        self._cache: dict[str, dict[str, Any]] = {}

    def _tool(self, name: str) -> Any:
        if name not in self._tools:
            msg = f"Live adapter needs `{name}`, which this server did not offer."
            raise CapabilityError(msg)
        return self._tools[name]

    async def _call(self, name: str, args: dict[str, Any]) -> Any:
        from wealth_agent.tools.spend import _parse_mcp_json

        return _parse_mcp_json(await self._tool(name).ainvoke(args))

    async def _account(self, account_number: str | None) -> str:
        if account_number:
            return account_number
        return resolve_account(await self._call("get_accounts", {}))

    async def _snapshot(self, account_number: str | None) -> dict[str, Any]:
        account = await self._account(account_number)
        if account in self._cache:
            return self._cache[account]

        raw = _rows(await self._call("get_equity_positions", {"account_number": account}),
                    "positions")
        # A closed position stays in the list at quantity 0. Reporting it as a
        # holding, or dividing by its zero cost basis, are both wrong.
        held = [r for r in raw if (as_float(r.get("quantity")) or 0) > 0]
        symbols = sorted({str(r["symbol"]) for r in held if r.get("symbol")})

        quotes: dict[str, tuple[float | None, str | None]] = {}
        for chunk in _chunks(symbols, QUOTES_CHUNK):
            for entry in _rows(await self._call("get_equity_quotes", {"symbols": chunk}),
                               "results"):
                sym = (entry.get("quote") or {}).get("symbol") or (
                    entry.get("close") or {}
                ).get("symbol")
                if sym:
                    quotes[str(sym)] = current_price(entry)

        profiles: dict[str, dict[str, Any]] = {}
        for chunk in _chunks(symbols, FUNDAMENTALS_CHUNK):
            for row in _rows(
                await self._call("get_equity_fundamentals", {"symbols": chunk}),
                "results",
                "fundamentals",
            ):
                if row.get("symbol"):
                    profiles[str(row["symbol"])] = row

        positions, as_of = [], None
        for row in held:
            symbol = str(row["symbol"])
            quantity = as_float(row.get("quantity")) or 0.0
            average_cost = as_float(row.get("average_buy_price"))
            last_price, stamp = quotes.get(symbol, (None, None))
            profile = profiles.get(symbol, {})
            as_of = max(as_of, stamp) if as_of and stamp else (as_of or stamp)

            entry: dict[str, Any] = {
                "symbol": symbol,
                "name": company_name(profile.get("description"), symbol),
                "sector": profile.get("sector") or "Unknown",
                "quantity": round(quantity, 6),
                "average_cost": _r2(average_cost),
                "last_price": _r2(last_price),
            }
            # Every derived figure is omitted rather than zeroed when an input
            # is missing. `market_value: 0.0` for a position we could not price
            # is a fabricated number, and the checker would ground it happily.
            if last_price is not None:
                entry["market_value"] = _r2(quantity * last_price)
            if average_cost is not None:
                entry["cost_basis"] = _r2(quantity * average_cost)
            if last_price is not None and average_cost is not None:
                pl = quantity * (last_price - average_cost)
                entry["unrealized_pl"] = _r2(pl)
                basis = quantity * average_cost
                entry["unrealized_pl_percent"] = _r2(pl / basis * 100) if basis else None
            positions.append(entry)

        positions.sort(key=lambda p: p.get("market_value") or 0.0, reverse=True)

        portfolio = unwrap(await self._call("get_portfolio", {"account_number": account}))
        portfolio = portfolio if isinstance(portfolio, dict) else {}
        priced = [p for p in positions if "cost_basis" in p and "market_value" in p]
        balances = {
            "account_number": account,
            "as_of": as_of or datetime.now(UTC).isoformat(),
            "total_value": _r2(as_float(portfolio.get("total_value"))),
            "equity_value": _r2(as_float(portfolio.get("equity_value"))),
            "cash": _r2(as_float(portfolio.get("cash"))),
            "buying_power": _r2(
                as_float((portfolio.get("buying_power") or {}).get("buying_power"))
                if isinstance(portfolio.get("buying_power"), dict)
                else portfolio.get("buying_power")
            ),
            "total_cost_basis": _r2(sum(p["cost_basis"] for p in priced)),
            "total_unrealized_pl": _r2(
                sum(p["market_value"] - p["cost_basis"] for p in priced)
            ),
            "positions_priced": len(priced),
            "positions_total": len(positions),
        }

        snapshot = {"positions": positions, "balances": balances}
        self._cache[account] = snapshot
        return snapshot

    def positions_tool(self) -> AdaptedTool:
        async def run(args: dict[str, Any]) -> dict[str, Any]:
            snapshot = await self._snapshot(args.get("account_number"))
            return {"positions": snapshot["positions"]}

        return AdaptedTool("get_positions", run)

    def balances_tool(self) -> AdaptedTool:
        async def run(args: dict[str, Any]) -> dict[str, Any]:
            snapshot = await self._snapshot(args.get("account_number"))
            return snapshot["balances"]

        return AdaptedTool("get_account_balances", run)


#: Field names the card feed might use, most specific first. The banking server
#: exposes the *agent virtual card* only — not a personal Robinhood card — so
#: for most accounts this returns an empty list, which is a true answer and not
#: an error.
_TXN_FIELDS: dict[str, tuple[str, ...]] = {
    "id": ("id", "transaction_id", "reference_id"),
    "date": ("date", "created_at", "transaction_date", "posted_at"),
    "amount": ("amount", "value", "settled_amount"),
    "description": ("description", "merchant", "merchant_name", "memo", "name"),
}


def adapt_card_transactions(tool: Any) -> AdaptedTool:
    """Normalize the agent-card feed into the fixture transaction shape."""

    async def run(args: dict[str, Any]) -> dict[str, Any]:
        from wealth_agent.tools.spend import _parse_mcp_json

        payload = _parse_mcp_json(await tool.ainvoke(args))
        rows = _rows(payload, "transactions", "results")
        out = []
        for row in rows:
            mapped: dict[str, Any] = {}
            for field, aliases in _TXN_FIELDS.items():
                value = next((row[a] for a in aliases if row.get(a) is not None), None)
                mapped[field] = value
            if mapped["amount"] is not None:
                mapped["amount"] = as_float(mapped["amount"])
            if isinstance(mapped["date"], str):
                mapped["date"] = mapped["date"][:10]
            out.append({**row, **{k: v for k, v in mapped.items() if v is not None}})
        return {"transactions": out}

    return AdaptedTool("get_card_transactions", run)


def _r2(value: float | None) -> float | None:
    return None if value is None else round(value + 1e-9, 2)


def resolve_capability_tools(by_name: dict[str, Any]) -> dict[str, Any]:
    """Return the three capability tools, adapting live ones when needed.

    The fixture names win when present, so demo mode never pays for any of this
    and the offline path stays byte-identical. Only a live server — which
    offers ``get_equity_positions`` and no ``get_positions`` — routes through
    the adapter.
    """
    resolved: dict[str, Any] = {}
    adapter: LiveAccountAdapter | None = None

    for capability, candidates in CAPABILITIES.items():
        native = candidates[0]
        if native in by_name:
            resolved[capability] = by_name[native]
            continue
        if capability == "card_transactions":
            live = next((by_name[c] for c in candidates[1:] if c in by_name), None)
            if live is None:
                msg = (
                    f"No tool provides `{capability}`. Looked for "
                    f"{', '.join(candidates)}; got {', '.join(sorted(by_name)) or '(nothing)'}."
                )
                raise CapabilityError(msg)
            resolved[capability] = adapt_card_transactions(live)
            continue
        adapter = adapter or LiveAccountAdapter(by_name)
        resolved[capability] = (
            adapter.positions_tool() if capability == "positions" else adapter.balances_tool()
        )
    return resolved


__all__ = [
    "AdaptedTool",
    "LiveAccountAdapter",
    "adapt_card_transactions",
    "as_float",
    "company_name",
    "current_price",
    "resolve_account",
    "resolve_capability_tools",
    "unwrap",
]
