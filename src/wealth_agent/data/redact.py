"""Deterministic redaction for anything captured from a live account.

Two rules govern this module:

* **Deterministic.** The same input always produces the same output, so a
  redacted capture stays internally consistent — an account number referenced
  from three different tool responses still matches itself afterwards. Random
  substitution would silently break every join in the data.
* **Structure-preserving.** Types and formats survive: a 9-digit account number
  redacts to a different 9-digit account number, not to ``"[REDACTED]"``. Tool
  schemas keep validating, and the agent behaves the same way on scrubbed data
  as on real data. A fixture that fails validation teaches you nothing.

The shipped fixtures under ``artifacts/`` are synthetic rather than redacted
(see :mod:`wealth_agent.data.synthetic`), so this module is what you reach for when
capturing *your own* account for private iteration.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

#: Salt for the pseudonymization hash. Changing it re-randomizes every mapping,
#: which is the intended "start over" lever.
_SALT = b"wealth-deep-agent/redact/v1"

#: Keys whose values are identifiers to be pseudonymized in place.
_ID_KEY_RE = re.compile(
    r"account(_|)(number|id)|^id$|_id$|url|email|phone|ssn|tax_id|"
    r"routing|card(_|)number|last_?4|holder|owner|address|street|zip|postal",
    re.IGNORECASE,
)

#: Keys whose values are free text that may embed a name or address.
_TEXT_KEY_RE = re.compile(r"name|description|memo|note|label", re.IGNORECASE)

#: Ticker symbols and similar public identifiers are not personal data and must
#: survive redaction, or the fixtures stop being about a real market.
_KEEP_KEY_RE = re.compile(
    r"^(symbol|ticker|instrument|currency|state|status|type|side|"
    r"category|merchant_category|exchange)$",
    re.IGNORECASE,
)


def _digest(value: str) -> str:
    return hashlib.blake2b(_SALT + value.encode(), digest_size=16).hexdigest()


def pseudonymize(value: str) -> str:
    """Map a string to a stable stand-in of the same shape.

    Digits map to digits and letters to letters, so ``"RH-4820193"`` becomes
    something like ``"XK-9174028"`` — same length, same layout, different data.
    """
    digest = _digest(value)
    out: list[str] = []
    for i, ch in enumerate(value):
        nibble = int(digest[i % len(digest)], 16)
        if ch.isdigit():
            out.append(str(nibble % 10))
        elif ch.isalpha():
            letter = chr(ord("A") + (nibble * 7 + i) % 26)
            out.append(letter if ch.isupper() else letter.lower())
        else:
            out.append(ch)
    return "".join(out)


def scrub(obj: Any, *, _key: str | None = None) -> Any:
    """Recursively redact identifiers in a decoded JSON structure.

    Args:
        obj: Any JSON-compatible value.
        _key: The key ``obj`` was found under; drives the redaction rules.

    Returns:
        A new structure with identifiers replaced and everything else intact.
    """
    if isinstance(obj, dict):
        return {k: scrub(v, _key=k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(v, _key=_key) for v in obj]

    if not isinstance(obj, str) or _key is None:
        return obj
    if _KEEP_KEY_RE.match(_key):
        return obj
    if _ID_KEY_RE.search(_key):
        return pseudonymize(obj)
    if _TEXT_KEY_RE.search(_key):
        return _scrub_free_text(obj)
    return obj


#: Conservative PII patterns for free-text fields.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "email"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "ssn"),
    (re.compile(r"\b(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}\b"), "phone"),
    (re.compile(r"\b\d{8,}\b"), "long_number"),
)


def _scrub_free_text(text: str) -> str:
    for pattern, _kind in _PATTERNS:
        text = pattern.sub(lambda m: pseudonymize(m.group(0)), text)
    return text


def looks_sensitive(text: str) -> list[str]:
    """Return the names of PII patterns present in ``text``.

    Used by the pre-publish check to fail loudly if anything unredacted is
    about to be committed.
    """
    return [kind for pattern, kind in _PATTERNS if pattern.search(text)]


__all__ = ["looks_sensitive", "pseudonymize", "scrub"]
