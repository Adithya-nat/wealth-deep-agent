"""Loop 0: measure the judge before you believe the judge.

Teams build an LLM-as-judge evaluator, run it over a dataset, get 0.82, and
start making decisions. Nobody asks the obvious question: *is 0.82 right?*

An LLM judge is an LLM application. It has a prompt, it has failure modes, and
it is the only component in the stack whose output is never itself checked. So
it gets the same treatment as everything else — a labeled set, a score, and a
before/after when you change it.

The metric is **agreement**: the fraction of labeled examples where the judge's
verdict matches the human's. Reported alongside its two halves, because a single
agreement number hides which way the judge is wrong:

* **false accept** — judge says grounded, human says not. The judge is
  agreeable, and it will bless hallucinations. This is the expensive direction.
* **false reject** — judge says not grounded, human says grounded. The judge is
  paranoid, and the team will learn to ignore it. Slower, equally fatal.

Run::

    uv run wealth evals judge-align            # both prompts, side by side
    uv run wealth evals judge-align --naive    # just the naive one

The workshop shows the naive prompt first, reads its mistakes out loud, and
derives the aligned prompt from them. The improvement is the point; the final
number is not.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wealth_agent.evals.evaluators import (
    ALIGNED_JUDGE_PROMPT,
    NAIVE_JUDGE_PROMPT,
    build_groundedness_judge,
    evidence_digest,
)
from wealth_agent.evals.fixtures import (
    MemoFixture,
    build_evidence_workspace,
    build_fixtures,
)
from wealth_agent.verify import verify_memo

DEFAULT_JUDGE_MODEL = "openai:gpt-5.5"


@dataclass
class Disagreement:
    fixture: MemoFixture
    judge_score: float
    judge_comment: str

    @property
    def direction(self) -> str:
        return "false_accept" if self.fixture.grounded is False else "false_reject"


@dataclass
class AlignmentResult:
    """How well one judge prompt reproduces human labels."""

    label: str
    total: int = 0
    agreed: int = 0
    disagreements: list[Disagreement] = field(default_factory=list)

    @property
    def agreement(self) -> float:
        return round(self.agreed / self.total, 4) if self.total else 0.0

    @property
    def false_accepts(self) -> list[Disagreement]:
        return [d for d in self.disagreements if d.direction == "false_accept"]

    @property
    def false_rejects(self) -> list[Disagreement]:
        return [d for d in self.disagreements if d.direction == "false_reject"]

    def summary(self) -> str:
        lines = [
            f"{self.label}: agreement {self.agreement:.0%} "
            f"({self.agreed}/{self.total})",
            f"  false accepts (blessed a bad memo): {len(self.false_accepts)}",
            f"  false rejects (flagged a good memo): {len(self.false_rejects)}",
        ]
        for d in self.disagreements:
            lines.append(
                f"    [{d.direction}] {d.fixture.id} (defect: {d.fixture.defect})\n"
                f"        human: {d.fixture.note}\n"
                f"        judge: {d.judge_comment[:200]}"
            )
        return "\n".join(lines)


def self_test(base: Path) -> dict[str, Any]:
    """Check the fixtures against the deterministic checker before grading.

    If a fixture labeled `grounded` fails the deterministic check, the label is
    wrong and every alignment number computed from it is noise. This runs first,
    always. The two known semantic misses are expected and named.
    """
    ws = build_evidence_workspace(base)
    expected_misses = {"u06-wrong-denominator", "u08-unsourced-percentage"}
    unexpected: list[str] = []
    for fixture in build_fixtures():
        report = verify_memo(fixture.memo, ws)
        if report.passed != fixture.grounded and fixture.id not in expected_misses:
            unexpected.append(fixture.id)
    return {
        "fixtures": len(build_fixtures()),
        "deterministic_misses_expected": sorted(expected_misses),
        "deterministic_misses_unexpected": unexpected,
        "ok": not unexpected,
    }


async def _score_one(judge: Any, fixture: MemoFixture, evidence: str) -> tuple[float, str]:
    result = await asyncio.to_thread(
        judge, outputs=fixture.memo, reference_outputs=evidence
    )
    score = result.get("score")
    numeric = 1.0 if score is True else 0.0 if score is False else float(score or 0.0)
    return numeric, str(result.get("comment") or "")


async def measure_alignment(
    prompt: str,
    label: str,
    *,
    base: Path,
    model: str = DEFAULT_JUDGE_MODEL,
    concurrency: int = 5,
) -> AlignmentResult:
    """Run one judge prompt over the labeled fixtures and score its agreement.

    Args:
        prompt: The judge prompt to measure.
        label: Human-readable name for this prompt version.
        base: Directory to build the evidence workspace under.
        model: Judge model. Kept in a different family from the agent under
            test so it does not share the agent's blind spots.
        concurrency: Parallel judge calls.
    """
    workspace = build_evidence_workspace(base)
    evidence = evidence_digest(workspace)
    judge = build_groundedness_judge(prompt, model=model)
    fixtures = build_fixtures()

    semaphore = asyncio.Semaphore(concurrency)

    async def run(fixture: MemoFixture) -> tuple[MemoFixture, float, str]:
        async with semaphore:
            score, comment = await _score_one(judge, fixture, evidence)
            return fixture, score, comment

    result = AlignmentResult(label=label, total=len(fixtures))
    for fixture, score, comment in await asyncio.gather(*(run(f) for f in fixtures)):
        judge_says_grounded = score >= 0.5
        if judge_says_grounded == fixture.grounded:
            result.agreed += 1
        else:
            result.disagreements.append(
                Disagreement(fixture=fixture, judge_score=score, judge_comment=comment)
            )
    return result


async def compare_prompts(
    *, base: Path, model: str = DEFAULT_JUDGE_MODEL
) -> tuple[AlignmentResult, AlignmentResult]:
    """Measure the naive and aligned prompts on the same fixtures.

    Sequential rather than concurrent: the point on stage is to watch the first
    number land, read the mistakes, and only then see the second.
    """
    naive = await measure_alignment(NAIVE_JUDGE_PROMPT, "naive prompt", base=base, model=model)
    aligned = await measure_alignment(
        ALIGNED_JUDGE_PROMPT, "aligned prompt", base=base, model=model
    )
    return naive, aligned


__all__ = [
    "DEFAULT_JUDGE_MODEL",
    "AlignmentResult",
    "Disagreement",
    "compare_prompts",
    "measure_alignment",
    "self_test",
]
