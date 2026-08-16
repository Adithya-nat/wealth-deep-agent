"""Turn raw card descriptors into merchants and categories, deterministically.

A card feed gives you ``SQ *BLUE BOTTLE COFFEE  OAKLAND`` and
``AMZN Mktp US*2K4LM  AMZN.COM/BILL``. Grouping spend requires knowing those are
"Blue Bottle Coffee" and "Amazon". An LLM can do it, and for the long tail it
has to — but running 285 rows through a model to learn that ``SQ *`` is a Square
prefix is slow, costs money, and gives a different answer next Tuesday.

So this is a three-tier pipeline, cheapest first:

1. **Strip the noise.** Processor prefixes, store numbers, city/state tails.
   Pure string work, no judgment involved.
2. **Match the rulebook.** An ordered list of regex → category rules. Covers
   the merchants that matter, and the ordering encodes precedence explicitly
   rather than hoping a model breaks ties consistently.
3. **Fall through to `Uncategorized`.** Which the agent can then reason about,
   with the row count small enough to be worth a model call.

The tier that matters for this workshop is the one that *doesn't* call a model:
categories feed spending totals, and totals end up in the memo as numbers a
human will act on. Deterministic in, checkable out.
"""

from __future__ import annotations

import re

#: Payment-processor and aggregator prefixes. These tell you how the charge was
#: routed, never who you bought from.
_PREFIXES = (
    r"^SQ\s*\*",
    r"^TST\*\s*",
    r"^SP\s+",
    r"^PY\s*\*",
    r"^PAYPAL\s*\*",
    r"^POS\s+",
    r"^DEBIT\s+",
    r"^CHECKCARD\s+",
    r"^PURCHASE\s+",
    r"^DOORDASH\s*\*",
    r"^UBER\s+\*",
)

_PREFIX_RE = re.compile("|".join(_PREFIXES), re.IGNORECASE)

#: Trailing junk: store numbers, transaction tags, city/state, phone, URLs.
_SUFFIXES = (
    r"\s+#?\d{2,}\s*$",
    r"\s+[A-Z]{2}\s*$",
    r"\s+\d{3}-\d{3}-\d{4}.*$",
    r"\s+(?:WWW\.|HTTPS?://)\S*$",
    r"\s+[A-Z]{2,}\.COM(?:/\S*)?$",
    r"\s*\*\s*\S+$",
)

_SUFFIX_RE = re.compile("|".join(_SUFFIXES), re.IGNORECASE)

#: Cities that appear as descriptor tails in this dataset's geography.
_CITY_RE = re.compile(
    r"\s+(?:MOUNTAIN VIEW|SAN FRANCISCO|SAN JOSE|PALO ALTO|SUNNYVALE|LOS GATOS|"
    r"OAKLAND|NEW YORK|SF|MTV)\b.*$",
    re.IGNORECASE,
)

#: Store numbers appearing mid-descriptor, e.g. `VENDOR LLC #442 SAN JOSE`.
_STORE_NO_RE = re.compile(r"\s+#\d{2,}\b")

#: (pattern, canonical merchant, category). Order is precedence: the first
#: match wins, so put the specific before the general. `APPLE.COM/BILL` must be
#: tested before a bare `APPLE`, or every iCloud charge lands in Shopping.
RULEBOOK: tuple[tuple[str, str, str], ...] = (
    (r"NETFLIX", "Netflix", "Entertainment"),
    (r"SPOTIFY", "Spotify", "Entertainment"),
    (r"GITHUB", "GitHub", "Software & Services"),
    (r"ANTHROPIC|CLAUDE", "Anthropic", "Software & Services"),
    (r"ICLOUD", "Apple", "Software & Services"),
    (r"APPLE STORE", "Apple", "Shopping"),
    (r"APPLE\.COM/BILL", "Apple", "Software & Services"),
    (r"\bAPPLE\b", "Apple", "Shopping"),
    (r"VERIZON", "Verizon", "Utilities"),
    (r"PG&E|PACIFIC GAS", "PG&E", "Utilities"),
    (r"EQUINOX", "Equinox", "Health & Fitness"),
    (r"STATE FARM", "State Farm", "Insurance"),
    (r"BLUE BOTTLE", "Blue Bottle Coffee", "Dining"),
    (r"PHILZ", "Philz Coffee", "Dining"),
    (r"TACOLICIOUS", "Tacolicious", "Dining"),
    (r"DOORDASH", "DoorDash", "Dining"),
    (r"AMZN|AMAZON", "Amazon", "Shopping"),
    (r"WHOLEFDS|WHOLE FOODS", "Whole Foods", "Groceries"),
    (r"TARGET", "Target", "Shopping"),
    (r"\bREI\b", "REI", "Shopping"),
    (r"UBER", "Uber", "Transportation"),
    (r"SHELL OIL|\bSHELL\b", "Shell", "Transportation"),
    (r"UNITED AIRLINES|UNITED\.COM", "United Airlines", "Travel"),
    (r"AUTOPAY|PAYMENT - THANK YOU", "Robinhood", "Payment"),
)

_COMPILED = tuple((re.compile(p, re.IGNORECASE), m, c) for p, m, c in RULEBOOK)

UNCATEGORIZED = "Uncategorized"

#: Categories that are not discretionary spend and must be excluded from
#: "how much did I spend" answers.
NON_SPEND_CATEGORIES = frozenset({"Payment"})


def clean_descriptor(raw: str) -> str:
    """Strip processor prefixes, store numbers, and geography from a descriptor."""
    text = raw.strip()
    text = _PREFIX_RE.sub("", text)
    text = _CITY_RE.sub("", text)
    text = _STORE_NO_RE.sub("", text)
    for _ in range(3):  # suffixes stack: "REI #4821  MOUNTAIN VIEW CA"
        new = _SUFFIX_RE.sub("", text).strip()
        if new == text:
            break
        text = new
    return re.sub(r"\s{2,}", " ", text).strip(" *-").strip()


def match_rulebook(raw: str) -> tuple[str, str] | None:
    """Return ``(merchant, category)`` for a descriptor, or ``None``.

    Matched against the *raw* descriptor rather than the cleaned one: cleaning
    can remove the very token that identifies the merchant (``AMZN.COM/BILL``
    is a URL suffix), so the rulebook gets first look at everything.
    """
    for pattern, merchant, category in _COMPILED:
        if pattern.search(raw):
            return merchant, category
    return None


def normalize(raw: str) -> tuple[str, str]:
    """Map a raw descriptor to ``(merchant, category)``.

    Falls back to the cleaned descriptor as the merchant name and
    :data:`UNCATEGORIZED` as the category, so an unmatched row is still
    groupable and still visible as a gap.
    """
    hit = match_rulebook(raw)
    if hit:
        return hit
    cleaned = clean_descriptor(raw)
    return (cleaned or raw.strip(), UNCATEGORIZED)


def merchant_key(raw: str) -> str:
    """Case- and punctuation-insensitive grouping key for a merchant name."""
    return re.sub(r"[^a-z0-9]+", "", normalize(raw)[0].lower())


__all__ = [
    "NON_SPEND_CATEGORIES",
    "RULEBOOK",
    "UNCATEGORIZED",
    "clean_descriptor",
    "match_rulebook",
    "merchant_key",
    "normalize",
]
