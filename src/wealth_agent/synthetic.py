"""Generate the synthetic account the demo runs against.

Why synthetic rather than a redacted capture of a real account: redaction
preserves structure but breaks arithmetic. Scale a position's market value and
it no longer equals quantity times price; leave it and you have published a real
balance. Since the whole workshop turns on *numbers being checkable*, the
fixtures have to be internally consistent — so they are generated from a seed,
with every derived figure actually derived.

Everything here is deterministic: same seed, same portfolio, same transactions,
same totals. That matters twice over. It keeps the demo reproducible on stage,
and it means the eval dataset's reference answers stay valid between runs.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

SEED = 20260819
ACCOUNT_NUMBER = "RH9042117"
AGENTIC_ACCOUNT_NUMBER = "RH9042118"
AS_OF = date(2026, 8, 14)

# (symbol, name, sector, shares, cost_basis_per_share, last_price)
_HOLDINGS: tuple[tuple[str, str, str, float, float, float], ...] = (
    ("NVDA", "NVIDIA Corporation", "Information Technology", 42.0, 118.40, 187.25),
    ("AAPL", "Apple Inc.", "Information Technology", 60.0, 191.05, 244.10),
    ("MSFT", "Microsoft Corporation", "Information Technology", 28.0, 402.75, 511.60),
    ("AVGO", "Broadcom Inc.", "Information Technology", 18.0, 151.20, 289.35),
    ("JPM", "JPMorgan Chase & Co.", "Financials", 35.0, 198.60, 262.80),
    ("V", "Visa Inc.", "Financials", 22.0, 271.15, 348.90),
    ("UNH", "UnitedHealth Group", "Health Care", 14.0, 512.30, 396.45),
    ("LLY", "Eli Lilly and Company", "Health Care", 9.0, 742.80, 918.20),
    ("COST", "Costco Wholesale", "Consumer Staples", 11.0, 682.40, 905.15),
    ("XOM", "Exxon Mobil Corporation", "Energy", 48.0, 108.95, 121.70),
    ("VOO", "Vanguard S&P 500 ETF", "Broad Market ETF", 55.0, 448.20, 592.85),
)

CASH_BALANCE = 18_420.55

# (raw descriptor, normalized merchant, category, amount, day-of-month, recurring)
_RECURRING: tuple[tuple[str, str, str, float, int], ...] = (
    ("NETFLIX.COM  LOS GATOS CA", "Netflix", "Entertainment", 22.99, 4),
    ("SPOTIFY USA  NEW YORK NY", "Spotify", "Entertainment", 11.99, 9),
    ("GITHUB.COM  SAN FRANCISCO", "GitHub", "Software & Services", 21.00, 12),
    ("ANTHROPIC CLAUDE  SF CA", "Anthropic", "Software & Services", 20.00, 12),
    ("VERIZON WIRELESS  PMT", "Verizon", "Utilities", 94.31, 17),
    ("PG&E  ELECTRIC PAYMENT", "PG&E", "Utilities", 141.08, 21),
    ("EQUINOX  MOUNTAIN VIEW CA", "Equinox", "Health & Fitness", 265.00, 2),
    ("STATE FARM INSURANCE  PMT", "State Farm", "Insurance", 178.44, 26),
    ("ICLOUD+ STORAGE  APPLE", "Apple", "Software & Services", 9.99, 6),
)

# (raw descriptor template, normalized merchant, category, low, high)
_DISCRETIONARY: tuple[tuple[str, str, str, float, float], ...] = (
    ("SQ *BLUE BOTTLE COFFEE  OAKLAND", "Blue Bottle Coffee", "Dining", 5.25, 18.50),
    ("AMZN Mktp US*{tag}  AMZN.COM/BILL", "Amazon", "Shopping", 12.40, 240.00),
    ("WHOLEFDS MTV#{tag}  MOUNTAIN VIEW", "Whole Foods", "Groceries", 34.10, 186.75),
    ("TST* TACOLICIOUS  SAN FRANCISCO", "Tacolicious", "Dining", 28.00, 96.00),
    ("UBER   *TRIP {tag}  HELP.UBER.COM", "Uber", "Transportation", 9.80, 62.40),
    ("SHELL OIL {tag}  MOUNTAIN VIEW CA", "Shell", "Transportation", 41.20, 78.90),
    ("TARGET   00{tag}  SUNNYVALE CA", "Target", "Shopping", 18.60, 214.30),
    ("DOORDASH*{tag}  WWW.DOORDASH.", "DoorDash", "Dining", 22.15, 78.40),
    ("UNITED AIRLINES {tag}  UNITED.COM", "United Airlines", "Travel", 218.00, 940.00),
    # Hardware uses a store descriptor; `APPLE.COM/BILL` is reserved for the
    # recurring iCloud charge above. Real feeds overload that one descriptor
    # for both, which is exactly the case a regex rulebook cannot settle.
    ("APPLE STORE R{tag}  PALO ALTO CA", "Apple", "Shopping", 29.00, 399.00),
    ("PHILZ COFFEE #{tag}  PALO ALTO", "Philz Coffee", "Dining", 4.75, 16.20),
    ("REI #{tag}  MOUNTAIN VIEW CA", "REI", "Shopping", 44.00, 320.00),
)

_SECTOR_BY_SYMBOL = {h[0]: h[2] for h in _HOLDINGS}


def _r2(value: float) -> float:
    return round(value + 1e-9, 2)


@dataclass
class Position:
    symbol: str
    name: str
    sector: str
    quantity: float
    average_cost: float
    last_price: float

    @property
    def market_value(self) -> float:
        return _r2(self.quantity * self.last_price)

    @property
    def cost_basis(self) -> float:
        return _r2(self.quantity * self.average_cost)

    @property
    def unrealized_pl(self) -> float:
        return _r2(self.market_value - self.cost_basis)

    @property
    def unrealized_pl_pct(self) -> float:
        return _r2(100 * self.unrealized_pl / self.cost_basis)

    def to_json(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "sector": self.sector,
            "quantity": self.quantity,
            "average_cost": self.average_cost,
            "last_price": self.last_price,
            "market_value": self.market_value,
            "cost_basis": self.cost_basis,
            "unrealized_pl": self.unrealized_pl,
            "unrealized_pl_percent": self.unrealized_pl_pct,
        }


@dataclass
class Portfolio:
    positions: list[Position] = field(default_factory=list)
    cash: float = CASH_BALANCE
    as_of: date = AS_OF

    @property
    def equity_value(self) -> float:
        return _r2(sum(p.market_value for p in self.positions))

    @property
    def total_value(self) -> float:
        return _r2(self.equity_value + self.cash)

    @property
    def total_cost_basis(self) -> float:
        return _r2(sum(p.cost_basis for p in self.positions))

    @property
    def total_unrealized_pl(self) -> float:
        return _r2(self.equity_value - self.total_cost_basis)

    def by_sector(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for p in self.positions:
            out[p.sector] = _r2(out.get(p.sector, 0.0) + p.market_value)
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def concentration(self) -> list[dict[str, Any]]:
        """Each position's share of total portfolio value, largest first."""
        total = self.total_value
        rows = [
            {
                "symbol": p.symbol,
                "market_value": p.market_value,
                "percent_of_portfolio": _r2(100 * p.market_value / total),
            }
            for p in self.positions
        ]
        return sorted(rows, key=lambda r: -r["percent_of_portfolio"])


def build_portfolio() -> Portfolio:
    """The deterministic demo portfolio."""
    return Portfolio(
        positions=[
            Position(sym, name, sector, qty, cost, price)
            for sym, name, sector, qty, cost, price in _HOLDINGS
        ]
    )


def build_transactions(months: int = 6) -> list[dict[str, Any]]:
    """Agentic-card transaction history, oldest first.

    Includes the three things a spend analyzer has to survive: recurring
    charges at a stable amount and day of month, messy raw descriptors that
    need normalizing before they can be grouped, and non-spend rows (refunds
    and statement payments) that must be excluded from spend totals rather than
    counted as negative spend.
    """
    rng = random.Random(SEED)
    rows: list[dict[str, Any]] = []
    seq = 0

    def add(day: date, raw: str, merchant: str, category: str, amount: float, kind: str) -> None:
        nonlocal seq
        seq += 1
        rows.append(
            {
                "id": f"txn_{seq:05d}",
                "date": day.isoformat(),
                "description": raw,
                "merchant": merchant,
                "category": category,
                "amount": _r2(amount),
                "type": kind,
            }
        )

    cursor = AS_OF.replace(day=1)
    for _ in range(months - 1):
        cursor = (cursor - timedelta(days=1)).replace(day=1)

    while cursor <= AS_OF:
        month_end = min(
            (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
            - timedelta(days=1),
            AS_OF,
        )

        for raw, merchant, category, amount, dom in _RECURRING:
            if dom > month_end.day:
                continue
            add(cursor.replace(day=dom), raw, merchant, category, amount, "charge")

        for _ in range(rng.randint(26, 38)):
            template, merchant, category, low, high = rng.choice(_DISCRETIONARY)
            day = cursor.replace(day=rng.randint(1, month_end.day))
            raw = template.format(tag=f"{rng.randint(1000, 9999)}")
            add(day, raw, merchant, category, rng.uniform(low, high), "charge")

        # One refund and one statement payment per month — the rows that break
        # naive "sum the amount column" spend math.
        if month_end.day >= 20:
            template, merchant, category, low, high = rng.choice(_DISCRETIONARY)
            add(
                cursor.replace(day=rng.randint(10, 20)),
                template.format(tag=f"{rng.randint(1000, 9999)}"),
                merchant,
                category,
                -rng.uniform(low, high),
                "refund",
            )
            add(
                cursor.replace(day=min(25, month_end.day)),
                "AUTOPAY PAYMENT - THANK YOU",
                "Robinhood",
                "Payment",
                -rng.uniform(1800, 4200),
                "payment",
            )

        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)

    rows.sort(key=lambda r: (r["date"], r["id"]))
    return rows


def build_orders(portfolio: Portfolio, count: int = 14) -> list[dict[str, Any]]:
    """Recent filled orders, consistent with the positions that exist."""
    rng = random.Random(SEED + 1)
    orders: list[dict[str, Any]] = []
    for i in range(count):
        pos = rng.choice(portfolio.positions)
        side = "buy" if rng.random() < 0.72 else "sell"
        qty = _r2(rng.uniform(1, max(2.0, pos.quantity / 4)))
        price = _r2(pos.average_cost * rng.uniform(0.88, 1.14))
        day = AS_OF - timedelta(days=rng.randint(3, 210))
        orders.append(
            {
                "id": f"ord_{i + 1:04d}",
                "symbol": pos.symbol,
                "side": side,
                "quantity": qty,
                "average_price": price,
                "total": _r2(qty * price),
                "state": "filled",
                "created_at": day.isoformat(),
            }
        )
    orders.sort(key=lambda o: o["created_at"], reverse=True)
    return orders


def quote_for(symbol: str, portfolio: Portfolio) -> dict[str, Any]:
    """A quote consistent with the position's last price."""
    for p in portfolio.positions:
        if p.symbol.upper() == symbol.upper():
            rng = random.Random(f"{SEED}:{symbol}")
            prev = _r2(p.last_price * rng.uniform(0.975, 1.02))
            return {
                "symbol": p.symbol,
                "name": p.name,
                "last_price": p.last_price,
                "previous_close": prev,
                "change": _r2(p.last_price - prev),
                "change_percent": _r2(100 * (p.last_price - prev) / prev),
                "sector": p.sector,
                "as_of": AS_OF.isoformat(),
            }
    return {"symbol": symbol.upper(), "error": "no quote available for this symbol"}


def sector_of(symbol: str) -> str | None:
    return _SECTOR_BY_SYMBOL.get(symbol.upper())


__all__ = [
    "ACCOUNT_NUMBER",
    "AGENTIC_ACCOUNT_NUMBER",
    "AS_OF",
    "CASH_BALANCE",
    "Portfolio",
    "Position",
    "build_orders",
    "build_portfolio",
    "build_transactions",
    "quote_for",
    "sector_of",
]
