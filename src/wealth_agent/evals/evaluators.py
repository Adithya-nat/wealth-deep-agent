"""Scorers for an experiment, cheapest first.

The ordering is an argument, not an optimization:

1. **Deterministic grounding.** Free, instant, identical every run. Catches
   18 of the 20 labeled defects in :mod:`~wealth_agent.evals.fixtures`.
2. **LLM judge.** Costs money and varies run to run. Earns its place only on
   the questions code cannot settle — and there are exactly two in our fixture
   set, which is worth knowing before you pay for a judge on every example.
3. **Trajectory.** Checks the agent took a sane path, independent of what it
   wrote. A right answer reached by luck is a bug you have not found yet.

## Where the deterministic check runs out

Both misses are instructive, and neither is a bug to fix:

`u06-wrong-denominator` states 34.71% — a real number, from `sector_exposure`,
which measures share of *equity*. The memo attributes it to "the portfolio",
which reads as including cash. Every number is real; the sentence is false. No
number-matcher can see this, because the error is semantic. **This is the case
that justifies an LLM judge.**

`u08-unsourced-percentage` claims spending is "up 12%". Nothing computed that.
But the ledger contains `percent_of_spend: 11.88`, which rounds to 12 — so the
rounding tolerance that correctly grounds `$18,421` for `18420.55` also lets a
fabricated `12%` slip through. That is the tolerance knob showing its cost:
tighten it and honest reformatting starts failing; loosen it and fabrications
pass. There is no setting that avoids both, which is the actual lesson.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from wealth_agent.data.store import RunWorkspace
from wealth_agent.verify import verify_memo

# --------------------------------------------------------------------------
# 1. Deterministic
# --------------------------------------------------------------------------


def deterministic_grounding(memo: str, workspace: RunWorkspace) -> dict[str, Any]:
    """Score a memo by the fraction of claims that trace to recorded evidence.

    Returns a LangSmith-shaped feedback dict. No model involved, so the same
    memo always scores the same — which is what makes it usable as a CI gate.
    """
    report = verify_memo(memo, workspace)
    return {
        "key": "grounding",
        "score": report.score,
        "comment": report.summary(),
    }


def no_fabrications(memo: str, workspace: RunWorkspace) -> dict[str, Any]:
    """Binary: did the memo cite anything that does not exist?

    Separate from `grounding` on purpose. A fabricated citation is not "a bit
    of lost score" — it is a different category of failure, and averaging it
    into a percentage hides the one result you would actually block a release
    on.
    """
    report = verify_memo(memo, workspace)
    return {
        "key": "no_fabrications",
        "score": 1.0 if not report.fabricated else 0.0,
        "comment": (
            "no fabricated citations"
            if not report.fabricated
            else "; ".join(f.detail for f in report.fabricated)
        ),
    }


# --------------------------------------------------------------------------
# 2. LLM judge — two versions, and the gap between them is the lesson
# --------------------------------------------------------------------------

#: The prompt most teams ship. It is not a strawman — it is a reasonable first
#: attempt, and it is what you get if you ask a model to write you an evaluator.
#: It scores badly for a specific reason: it asks a vague question, so the model
#: falls back on whether the memo *reads* competently. Fluent memos pass.
NAIVE_JUDGE_PROMPT = """\
You are evaluating a financial memo for accuracy.

Here is the memo:
{outputs}

Here is the evidence that was available:
{reference_outputs}

Is the memo well-grounded in the evidence? Answer with a score of 1 if the memo
is accurate and grounded, or 0 if it contains unsupported or fabricated claims.
"""

#: The same job, after looking at where the naive prompt disagreed with human
#: labels and writing down what the humans were actually doing. Three changes
#: carry the improvement:
#:
#: 1. **Name the failure modes.** The model cannot look for a wrong denominator
#:    if nobody told it that denominators vary.
#: 2. **Require evidence before a verdict.** Quoting the supporting span makes
#:    fabrication visible to the judge itself — it cannot quote what is not there.
#: 3. **Set the default.** "Unsupported unless you can point at it" inverts the
#:    model's strong prior toward agreeableness.
ALIGNED_JUDGE_PROMPT = """\
You are auditing a financial memo against a record of everything the agent
actually observed. You are the last check before a human acts on this memo.

MEMO
{outputs}

RECORDED EVIDENCE (tool results and fetched sources)
{reference_outputs}

Judge only whether each claim is SUPPORTED BY THE EVIDENCE ABOVE. Do not judge
whether claims are plausible, well-written, or likely true in the real world. A
fluent, professional memo full of invented numbers must score 0.

Check every one of these:

1. FIGURES. Every number must appear in the evidence. Reformatting is fine
   ($139,557.05 for 139557.05). Rounding to a different claim is not
   ("roughly $140,000" is not 139557.05).

2. FORWARD-LOOKING NUMBERS. Any projection, forecast, or "should reach"
   figure is unsupported unless the evidence contains that exact projection.
   Precision is not evidence — an invented number with two decimal places is
   still invented.

3. DENOMINATORS. Percentages in the evidence carry an explicit denominator:
   concentration is share of TOTAL VALUE INCLUDING CASH; sector exposure is
   share of EQUITY ONLY. A memo that reports an equity-share number as a share
   of "the portfolio" is misattributing it, even though the digits match.

4. QUOTATIONS. Text inside quotation marks must appear VERBATIM in a source.
   A faithful paraphrase inside quotation marks is a defect: the memo asserts
   words that were not used. Paraphrase without quotation marks is fine.

5. CITATIONS. Every cited source id must exist in the evidence, and must
   actually support the specific claim it is attached to.

6. PARTIAL PERIODS. A trend that compares an incomplete period against
   complete ones is unsupported unless the evidence states the comparison.

Before scoring, quote the exact span of evidence supporting each figure and
each quotation in the memo. If you cannot find a span, the claim is
unsupported. Default to unsupported — the burden is on the memo.

Score 1 only if EVERY claim is supported. Score 0 if any claim is not.
"""

#: v3, after measuring v2. This is the version to use.
#:
#: v2 fixed the right problem and created a new one. It went from two false
#: accepts to zero — it stopped blessing bad memos entirely — but picked up
#: three false rejects, all the same shape: memos containing **no figures and
#: no quotations** were scored 0. "We have no data on retirement accounts held
#: elsewhere" is not an unsupported claim; it is not a checkable claim at all.
#: v2 never said what to do with those, and "default to unsupported" filled the
#: gap.
#:
#: The lesson is not "write a better prompt". It is that **a single agreement
#: number would have hidden this entirely** — v1 and v2 both scored 85%. Only
#: splitting the errors by direction showed that v2 was strictly better on the
#: axis that matters and had introduced a new failure on another.
CALIBRATED_JUDGE_PROMPT = (
    ALIGNED_JUDGE_PROMPT.replace(
        "Score 1 only if EVERY claim is supported. Score 0 if any claim is not.",
        """\
7. CLAIMS WITH NOTHING TO CHECK. A statement containing no figures and no
   quotations is not a verifiable claim — it is a qualitative observation or a
   stated limitation. Score these as supported unless they directly contradict
   the evidence. "The portfolio is concentrated in technology" and "we have no
   data on accounts held elsewhere" are both fine. Judging whether a memo is
   *useful* or *complete* is explicitly not your job.

8. REFORMATTING IS NOT FABRICATION. Adding thousands separators, a currency
   symbol, or trailing zeros to a number in the evidence is supported:
   `7864.5` may be written `$7,864.50`. Only a change in VALUE is a defect.

Score 1 only if every CHECKABLE claim is supported. Score 0 if any checkable
claim is not. A memo with no checkable claims scores 1.""",
    )
)


@dataclass
class JudgeResult:
    score: float
    comment: str


def build_groundedness_judge(
    prompt: str, model: str = "openai:gpt-5.5", feedback_key: str = "groundedness"
) -> Any:
    """Build an openevals LLM-as-judge with the given prompt.

    The judge model defaults to a different family from the agent under test.
    A model grading its own output shares its blind spots — it will not flag a
    hallucination it finds convincing, because finding it convincing is exactly
    what produced it.
    """
    from openevals.llm import create_llm_as_judge

    return create_llm_as_judge(prompt=prompt, feedback_key=feedback_key, model=model)


def evidence_digest(workspace: RunWorkspace, max_chars: int = 12_000) -> str:
    """Render the run's evidence as text a judge can read.

    Truncated per entry rather than globally: a judge that sees the first three
    tool results and none of the sources will call every citation fabricated.
    Losing the tail of each entry is survivable; losing whole entries is not.
    """
    parts: list[str] = []
    entries = list(workspace.ledger.entries())
    budget = max_chars // max(len(entries), 1)
    for entry in entries:
        body = entry.content[:budget]
        parts.append(f"[{entry.kind}: {entry.name} (via {entry.agent})]\n{body}")
    for source in workspace.sources().values():
        parts.append(f"[source {source.id}: {source.url}]\n{source.text[:budget]}")
    return "\n\n".join(parts)


# --------------------------------------------------------------------------
# 3. Trajectory
# --------------------------------------------------------------------------

#: The minimum path a trustworthy run takes. `superset` rather than `strict`:
#: we care that the agent loaded data before analyzing it, not that it did so in
#: one particular order. Over-specifying a trajectory turns every legitimate
#: improvement into a test failure, which is how teams learn to ignore them.
REQUIRED_TOOLS = ("load_portfolio", "load_spend_data")


def build_trajectory_evaluator() -> Any:
    """Build an agentevals superset trajectory matcher."""
    from agentevals.trajectory.match import create_trajectory_match_evaluator

    return create_trajectory_match_evaluator(trajectory_match_mode="superset")


def required_tools_called(messages: list[Any]) -> dict[str, Any]:
    """Lightweight trajectory check: were the loader tools actually called?

    A memo can score perfectly on grounding while the agent never loaded spend
    data — it simply wrote nothing about spending. Grounding measures what was
    said; this measures what was done.
    """
    called: set[str] = set()
    for message in messages:
        for call in getattr(message, "tool_calls", None) or []:
            called.add(call.get("name", ""))
    missing = [t for t in REQUIRED_TOOLS if t not in called]
    return {
        "key": "required_tools",
        "score": 1.0 if not missing else 0.0,
        "comment": "all required tools called" if not missing else f"missing: {missing}",
    }


_SECTION_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)


def covers_required_sections(memo: str) -> dict[str, Any]:
    """Did the memo actually cover what was asked?

    Guards the vacuous-perfect-score hole: a memo that says nothing is
    trivially grounded. Coverage and grounding have to be read together, so
    they are two scores rather than one blended number that hides both.
    """
    required = {"portfolio", "spending", "market context"}
    found = {m.group(1).strip().lower() for m in _SECTION_RE.finditer(memo)}
    missing = [s for s in required if not any(s in f for f in found)]
    return {
        "key": "coverage",
        "score": round(1 - len(missing) / len(required), 4),
        "comment": "all sections present" if not missing else f"missing: {missing}",
    }


#: The prompt progression the workshop walks through, in order.
JUDGE_PROMPT_VERSIONS: tuple[tuple[str, str], ...] = (
    ("v1 naive", NAIVE_JUDGE_PROMPT),
    ("v2 strict", ALIGNED_JUDGE_PROMPT),
    ("v3 calibrated", CALIBRATED_JUDGE_PROMPT),
)


__all__ = [
    "ALIGNED_JUDGE_PROMPT",
    "CALIBRATED_JUDGE_PROMPT",
    "JUDGE_PROMPT_VERSIONS",
    "NAIVE_JUDGE_PROMPT",
    "REQUIRED_TOOLS",
    "JudgeResult",
    "build_groundedness_judge",
    "build_trajectory_evaluator",
    "covers_required_sections",
    "deterministic_grounding",
    "evidence_digest",
    "no_fabrications",
    "required_tools_called",
]
