"""Deterministic verification of a memo against what the agent actually saw.

No LLM runs in this module. That is the point.

The instinct when an agent hallucinates is to add a second agent that checks the
first one. Sometimes you need that. But a large share of the damage in a
research or analysis agent comes from two failure modes that are *mechanically*
detectable, and paying a model to detect them is slower, more expensive, and
less reliable than twenty lines of Python:

**Citation drift.** The memo cites ``src_a91f`` for a claim, but ``src_a91f``
was never fetched — or was fetched and does not contain the quoted span. This
is not the model "lying"; it is what happens when a subagent compresses six
sources into a summary and the supervisor re-attributes from the summary. The
bug lives in the seam between two context windows, which is exactly the seam a
human reviewer cannot see.

**Fabricated figures.** The memo states a number that appears in no tool result
and no fetched page. Models are excellent at producing plausible numbers and
silently rounding real ones into wrong ones. In a financial memo this is the
error that costs you.

Both checks answer a question with one right answer, so they get code. The
judgment calls — is this analysis *useful*, is the tone right — are what an LLM
judge is for. Order matters: run the cheap deterministic check first, and only
spend model tokens on what survives.

The verdict taxonomy deliberately distinguishes *unsupported* from *fabricated*.
"I cannot find this" and "this contradicts what we saw" call for different human
responses, and collapsing them into a single "fail" throws away the signal.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from wealth_agent.store import Figure, RunWorkspace, extract_figures, is_grounded


class Verdict(StrEnum):
    """Per-claim outcome, ordered from best to worst."""

    GROUNDED = "grounded"
    """Traces to a recorded tool result or a fetched source."""

    UNSUPPORTED = "unsupported"
    """Nothing recorded supports it. May be true; we cannot tell."""

    FABRICATED = "fabricated"
    """Cites a source that was never fetched, or quotes text not in it."""


#: `[src_a91f0c2d]` — the citation form the memo-format skill requires.
CITATION_RE = re.compile(r"\[(src_[0-9a-f]{8})\]")

#: A quoted span attributed to a source: `"..." [src_xxxx]`.
QUOTED_CLAIM_RE = re.compile(r"[\"“]([^\"”]{12,400})[\"”]\s*\[(src_[0-9a-f]{8})\]")

#: Lines that are structure, not claims.
_SKIP_LINE_RE = re.compile(r"^\s*(#{1,6}\s|\||[-*_]{3,}\s*$|```)")


@dataclass
class Finding:
    """One thing the verifier has an opinion about."""

    verdict: Verdict
    kind: str  # "citation" | "figure"
    detail: str
    excerpt: str
    line: int

    def to_json(self) -> dict[str, Any]:
        return {**asdict(self), "verdict": str(self.verdict)}


@dataclass
class VerificationReport:
    """Aggregate result. ``score`` is what the eval harness consumes."""

    findings: list[Finding] = field(default_factory=list)
    checked_citations: int = 0
    checked_figures: int = 0

    @property
    def failures(self) -> list[Finding]:
        return [f for f in self.findings if f.verdict is not Verdict.GROUNDED]

    @property
    def fabricated(self) -> list[Finding]:
        return [f for f in self.findings if f.verdict is Verdict.FABRICATED]

    @property
    def checked(self) -> int:
        return self.checked_citations + self.checked_figures

    @property
    def score(self) -> float:
        """Fraction of checkable claims that are grounded, in ``[0, 1]``.

        A memo with nothing to check scores 1.0 — vacuously grounded. That is
        the honest reading, and the eval dataset guards against gaming it by
        requiring a minimum claim count separately.
        """
        if self.checked == 0:
            return 1.0
        return round(1.0 - len(self.failures) / self.checked, 4)

    @property
    def passed(self) -> bool:
        """No fabrications and at least 95% of claims grounded."""
        return not self.fabricated and self.score >= 0.95

    def summary(self) -> str:
        if not self.checked:
            return "No checkable claims found in the memo."
        lines = [
            f"grounding score: {self.score:.2%} "
            f"({self.checked - len(self.failures)}/{self.checked} claims grounded)",
            f"citations checked: {self.checked_citations} | "
            f"figures checked: {self.checked_figures}",
        ]
        if self.fabricated:
            lines.append(f"FABRICATED: {len(self.fabricated)}")
        for finding in self.failures[:20]:
            lines.append(
                f"  [{finding.verdict}] line {finding.line}: {finding.detail}\n"
                f"      {finding.excerpt[:120]}"
            )
        if len(self.failures) > 20:
            lines.append(f"  ... and {len(self.failures) - 20} more")
        return "\n".join(lines)

    def to_json(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "passed": self.passed,
            "checked_citations": self.checked_citations,
            "checked_figures": self.checked_figures,
            "failures": [f.to_json() for f in self.failures],
            "summary": self.summary(),
        }


def _normalize(text: str) -> str:
    """Collapse whitespace and smart punctuation so quote matching is not
    defeated by a model turning ' into ’ or wrapping a line."""
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip().lower()


def check_citations(memo: str, workspace: RunWorkspace) -> list[Finding]:
    """Verify every cited source exists, and every quoted span is really in it.

    Each citation produces exactly one finding, even though two conditions are
    tested. Emitting a separate finding for the quote mismatch would count the
    same claim twice — once grounded, once fabricated — and a single bad quote
    would drag the score to 50% while also inflating the denominator.
    """
    sources = workspace.sources()
    normalized = {sid: _normalize(src.text) for sid, src in sources.items()}
    lines = memo.splitlines()

    # Citations that carry a quoted span, keyed by the offset of their '['.
    # QUOTED_CLAIM_RE's group(2) starts one character after that bracket.
    quoted: dict[int, str] = {
        m.start(2) - 1: m.group(1) for m in QUOTED_CLAIM_RE.finditer(memo)
    }

    findings: list[Finding] = []
    for match in CITATION_RE.finditer(memo):
        sid = match.group(1)
        lineno = memo[: match.start()].count("\n") + 1
        excerpt = lines[lineno - 1].strip() if lineno <= len(lines) else ""
        quote = quoted.get(match.start())

        if sid not in sources:
            verdict, detail = (
                Verdict.FABRICATED,
                f"cites {sid}, which was never fetched",
            )
        elif quote is not None and _normalize(quote) not in normalized[sid]:
            verdict, detail = (
                Verdict.FABRICATED,
                f"quoted span does not appear in {sid}",
            )
            excerpt = quote
        else:
            verdict, detail = Verdict.GROUNDED, f"{sid} was fetched"

        findings.append(
            Finding(
                verdict=verdict,
                kind="citation",
                detail=detail,
                excerpt=excerpt,
                line=lineno,
            )
        )
    return findings


def check_figures(memo: str, workspace: RunWorkspace) -> list[Finding]:
    """Verify every figure in the memo traces to something the agent observed."""
    grounded = workspace.grounded_values()
    findings: list[Finding] = []

    for lineno, line in enumerate(memo.splitlines(), start=1):
        if _SKIP_LINE_RE.match(line):
            continue
        for fig in extract_figures(line):
            verdict = (
                Verdict.GROUNDED if is_grounded(fig.value, grounded) else Verdict.UNSUPPORTED
            )
            findings.append(
                Finding(
                    verdict=verdict,
                    kind="figure",
                    detail=(
                        f"{fig.raw} traces to a recorded observation"
                        if verdict is Verdict.GROUNDED
                        else f"{fig.raw} appears in no tool result or fetched source"
                    ),
                    excerpt=line.strip(),
                    line=lineno,
                )
            )
    return findings


def verify_memo(memo: str, workspace: RunWorkspace) -> VerificationReport:
    """Run both deterministic checks over a memo.

    Args:
        memo: The markdown memo the agent produced.
        workspace: The run whose ledger and sources are the ground truth.

    Returns:
        A report whose ``score`` is the fraction of checkable claims that hold
        up, and whose ``failures`` name each one that does not.
    """
    citations = check_citations(memo, workspace)
    figures = check_figures(memo, workspace)
    return VerificationReport(
        findings=[*citations, *figures],
        checked_citations=len(citations),
        checked_figures=len(figures),
    )


__all__ = [
    "CITATION_RE",
    "Figure",
    "Finding",
    "VerificationReport",
    "Verdict",
    "check_citations",
    "check_figures",
    "verify_memo",
]
