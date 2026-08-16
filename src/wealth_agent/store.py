"""The agent's workspace on disk, and the ledger that makes its claims checkable.

Every deep agent has a filesystem. This module decides what goes in it and, more
importantly, records *where every fact came from*.

The design rule the whole workshop rests on:

    You cannot evaluate what you did not record.

An agent that reads a balance, summarizes it through a subagent, and writes a
memo has destroyed the link between the number on the page and the API response
it came from — unless something wrote that link down at the time. The
:class:`GroundingLedger` is that something. It is append-only, it is written by
middleware rather than by the tools themselves (so a new tool cannot forget to
participate), and it is the single source of truth for
:mod:`wealth_agent.verify`.

Using a real directory rather than in-state files is deliberate. It costs a
little safety — hence ``virtual_mode=True`` on the backend — and buys three
things: subagents and the verifier share one view without any state-propagation
puzzle, runs survive a crash, and you can put the agent's entire working memory
on screen with ``ls``.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from wealth_agent.config import REPO_ROOT

RUNS_DIR = REPO_ROOT / "runs"

#: Subdirectories every run gets. Named for *who writes them*, so a trace and a
#: directory listing tell the same story.
PORTFOLIO_DIR = "portfolio"
SPEND_DIR = "spend"
SOURCES_DIR = "sources"

LEDGER_FILE = "ledger.jsonl"
MEMO_FILE = "memo.md"


def new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S")


# --------------------------------------------------------------------------
# Number extraction — the shared vocabulary between the memo and the ledger
# --------------------------------------------------------------------------

#: Currency amounts, percentages, and bare decimals. Deliberately greedy about
#: thousands separators because that is exactly how a model reformats a raw API
#: number, and reformatting must not count as fabrication.
_NUMBER_RE = re.compile(
    r"""
    (?<![\w.])          # not mid-identifier
    -?                  # optional sign
    \$?                 # optional currency marker
    \d{1,3}(?:,\d{3})+  # 1,234,567
    (?:\.\d+)?
    |
    (?<![\w.])
    -?\$?\d+(?:\.\d+)?  # 1234.56
    """,
    re.VERBOSE,
)

#: Four-digit years are almost always dates, not claims. Excluding them removes
#: the single largest source of false positives.
_YEAR_RANGE = range(1990, 2101)

#: Integers below this are counts, list indices, and ordinals ("3 sectors").
#: They are excluded unless they carry a `$` or `%`, which turns them back into
#: a claim. This threshold is the main tuning knob of the numeric check — see
#: `docs` in verify.py for how to reason about moving it.
_BARE_INT_FLOOR = 100


@dataclass(frozen=True)
class Figure:
    """A numeric claim lifted out of text, with the form it was written in."""

    value: float
    raw: str
    is_currency: bool
    is_percent: bool

    @property
    def is_bare_int(self) -> bool:
        return (
            not self.is_currency
            and not self.is_percent
            and float(self.value).is_integer()
        )


def extract_figures(text: str, *, include_trivial: bool = False) -> list[Figure]:
    """Pull numeric claims out of prose.

    Args:
        text: Any text — a memo, a tool result, a fetched page.
        include_trivial: Keep years, small counts, and list indices. Off for
            memo claims (too noisy), on when building the grounded set (a
            number that is trivial in a memo may still be the number a tool
            returned).

    Returns:
        Figures in document order, duplicates included.
    """
    out: list[Figure] = []
    for match in _NUMBER_RE.finditer(text):
        raw = match.group(0)
        tail = text[match.end() : match.end() + 1]
        is_percent = tail == "%"
        is_currency = raw.lstrip("-").startswith("$")
        try:
            value = float(raw.replace("$", "").replace(",", ""))
        except ValueError:  # pragma: no cover — regex guarantees parseability
            continue

        if not include_trivial:
            if float(value).is_integer() and int(value) in _YEAR_RANGE:
                continue
            if (
                not is_currency
                and not is_percent
                and float(value).is_integer()
                and abs(value) < _BARE_INT_FLOOR
            ):
                continue
        out.append(Figure(value=value, raw=raw, is_currency=is_currency, is_percent=is_percent))
    return out


def _quantize(value: float) -> tuple[float, ...]:
    """Forms a number may legitimately be rewritten as.

    A tool returns ``7864.5``; a memo may say ``$7,864.50``, ``$7,864.5``, or
    ``$7,865``. All three are the same claim. Rounding to 2, 1, and 0 decimals
    covers the reformatting a model actually does without opening the door to
    ``$8,000`` passing as ``7864.5``.
    """
    return (round(value, 2), round(value, 1), float(round(value)))


def grounded_values(texts: list[str]) -> set[float]:
    """Every numeric value observable in the supplied source texts."""
    values: set[float] = set()
    for text in texts:
        for fig in extract_figures(text, include_trivial=True):
            values.update(_quantize(fig.value))
    return values


def is_grounded(value: float, grounded: set[float]) -> bool:
    """True if ``value`` matches an observed number in any reasonable rounding."""
    return any(form in grounded for form in _quantize(value))


# --------------------------------------------------------------------------
# Ledger
# --------------------------------------------------------------------------


@dataclass
class LedgerEntry:
    """One thing the agent observed."""

    kind: str  # "tool_result" | "source"
    name: str  # tool name, or source id
    agent: str  # which (sub)agent observed it
    args: dict[str, Any]
    content: str
    at: float

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "agent": self.agent,
            "args": self.args,
            "content": self.content,
            "at": self.at,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> LedgerEntry:
        return cls(
            kind=raw["kind"],
            name=raw["name"],
            agent=raw.get("agent", "unknown"),
            args=raw.get("args", {}),
            content=raw.get("content", ""),
            at=raw.get("at", 0.0),
        )


class GroundingLedger:
    """Append-only record of everything the agent observed during one run.

    Append-only on purpose: a ledger an agent can rewrite is not evidence. The
    file is opened in append mode for every write so concurrent subagents
    cannot clobber each other's entries.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def record(
        self,
        *,
        kind: str,
        name: str,
        content: str,
        agent: str = "supervisor",
        args: dict[str, Any] | None = None,
    ) -> None:
        entry = LedgerEntry(
            kind=kind,
            name=name,
            agent=agent,
            args=args or {},
            content=content,
            at=time.time(),
        )
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry.to_json()) + "\n")

    def entries(self) -> Iterator[LedgerEntry]:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield LedgerEntry.from_json(json.loads(line))

    def texts(self) -> list[str]:
        return [e.content for e in self.entries()]

    def grounded_values(self) -> set[float]:
        return grounded_values(self.texts())

    def by_agent(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.entries():
            counts[entry.agent] = counts.get(entry.agent, 0) + 1
        return counts


# --------------------------------------------------------------------------
# Workspace
# --------------------------------------------------------------------------


def source_id(url: str) -> str:
    """Stable, short id for a URL. Same URL always yields the same file."""
    return "src_" + hashlib.blake2b(url.encode(), digest_size=4).hexdigest()


@dataclass
class Source:
    id: str
    url: str
    title: str
    text: str


class RunWorkspace:
    """One run's directory: the agent's working memory, on disk and inspectable."""

    def __init__(self, run_id: str | None = None, base: Path | None = None) -> None:
        self.run_id = run_id or new_run_id()
        self.root = (base or RUNS_DIR) / self.run_id
        for sub in (PORTFOLIO_DIR, SPEND_DIR, SOURCES_DIR):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        self.ledger = GroundingLedger(self.root / LEDGER_FILE)

    # -- sources ----------------------------------------------------------

    def write_source(self, url: str, title: str, text: str) -> Source:
        """Persist a fetched page and record it in the ledger.

        Returns the :class:`Source` so the caller can hand the agent the id
        rather than the body — the point of offloading is that the full text
        never has to enter a context window to be citable.
        """
        sid = source_id(url)
        payload = f"---\nid: {sid}\nurl: {url}\ntitle: {title}\n---\n\n{text}"
        (self.root / SOURCES_DIR / f"{sid}.md").write_text(payload, encoding="utf-8")
        self.ledger.record(
            kind="source", name=sid, content=text, agent="market-researcher",
            args={"url": url, "title": title},
        )
        return Source(id=sid, url=url, title=title, text=text)

    def sources(self) -> dict[str, Source]:
        out: dict[str, Source] = {}
        for path in sorted((self.root / SOURCES_DIR).glob("src_*.md")):
            raw = path.read_text(encoding="utf-8")
            meta, _, body = raw.partition("\n\n")
            fields = dict(
                line.split(": ", 1)
                for line in meta.splitlines()
                if ": " in line and not line.startswith("---")
            )
            out[path.stem] = Source(
                id=path.stem,
                url=fields.get("url", ""),
                title=fields.get("title", ""),
                text=body,
            )
        return out

    # -- memo -------------------------------------------------------------

    @property
    def memo_path(self) -> Path:
        return self.root / MEMO_FILE

    def read_memo(self) -> str:
        return self.memo_path.read_text(encoding="utf-8") if self.memo_path.exists() else ""

    def write_memo(self, text: str) -> None:
        self.memo_path.write_text(text, encoding="utf-8")

    # -- grounding --------------------------------------------------------

    def grounded_values(self) -> set[float]:
        """Every number the agent legitimately observed this run."""
        return self.ledger.grounded_values()

    def describe(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "root": str(self.root),
            "ledger_entries": sum(1 for _ in self.ledger.entries()),
            "by_agent": self.ledger.by_agent(),
            "sources": len(self.sources()),
            "has_memo": self.memo_path.exists(),
        }


def latest_run(base: Path | None = None) -> RunWorkspace | None:
    """Reopen the most recent run, for inspecting or re-verifying after the fact."""
    base = base or RUNS_DIR
    if not base.exists():
        return None
    runs = sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.name)
    return RunWorkspace(run_id=runs[-1].name, base=base) if runs else None


__all__ = [
    "Figure",
    "GroundingLedger",
    "LedgerEntry",
    "RUNS_DIR",
    "RunWorkspace",
    "Source",
    "extract_figures",
    "grounded_values",
    "is_grounded",
    "latest_run",
    "new_run_id",
    "source_id",
]
