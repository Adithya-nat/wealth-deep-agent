"""Run the free check first. Only pay for a model when it fails.

This repo argues that you should not use a judge where code will do, and then
— in its first version — called an LLM verifier and an LLM rubric grader on
every single run, including the runs where the deterministic checker had
nothing to complain about. The argument was right and the implementation did
not follow it.

The gate closes that. After the agent finishes, `verify_memo` runs: no model,
no network, a few milliseconds. If the memo is clean, the run ends there and
the LLM verification path never executes. If it is not, the specific findings
go back to the agent as a message — naming the line and the claim — and it gets
a bounded number of attempts to fix them.

On a clean run this removes an entire subagent and a grading loop from the bill.
On a dirty one it costs the same as before and produces better feedback, because
"line 47 cites src_9f2201aa, which was never fetched" is a more actionable note
than a grader's paraphrase of it.

The general shape is worth more than the saving: **order your checks by cost and
let the cheap ones short-circuit.** It applies to any evaluation stack, and it
is the opposite of what most teams build, which is a judge that runs first and
always.
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage

from wealth_agent.data.store import RunWorkspace
from wealth_agent.middleware.events import emit
from wealth_agent.verify import Verdict, verify_memo

#: How many times the agent may be sent back to fix its own memo. Two is a
#: judgement: one revision fixes the common case, a second catches what the
#: first introduced, and beyond that the failures are usually not fixable by
#: rewording — they need a tool that returns the missing figure.
MAX_REVISIONS = 2


def findings_message(report: Any, attempt: int) -> str:
    """Turn a verification report into instructions the agent can act on."""
    lines = [
        f"Your memo did not pass verification (attempt {attempt} of {MAX_REVISIONS}).",
        f"Grounding score {report.score:.2%}; {len(report.failures)} claims failed.",
        "",
    ]
    fabricated = [f for f in report.failures if f.verdict is Verdict.FABRICATED]
    unsupported = [f for f in report.failures if f.verdict is Verdict.UNSUPPORTED]

    if fabricated:
        lines.append("FABRICATED — these are serious. Remove or re-attribute each one:")
        for finding in fabricated[:10]:
            lines.append(f"  line {finding.line}: {finding.detail}")
            lines.append(f"    {finding.excerpt[:160]}")
        lines.append("")
    if unsupported:
        lines.append(
            "UNSUPPORTED — no recorded tool result contains these figures. For each, "
            "either replace it with a figure a tool actually returned, delegate to get "
            "that figure, or move the claim to 'What we could not verify':"
        )
        for finding in unsupported[:12]:
            lines.append(f"  line {finding.line}: {finding.detail}")
        lines.append("")
    lines.append(
        "Rewrite /memo.md addressing every item above. Do not soften a claim until it "
        "stops being checkable — remove it, re-source it, or say it is unverified."
    )
    return "\n".join(lines)


def revision_request(
    workspace: RunWorkspace, attempt: int
) -> tuple[Any, HumanMessage | None]:
    """Check the memo and, if it fails, build the message that asks for a fix.

    Returns ``(report, message)``; ``message`` is ``None`` when the memo passes.

    This is a plain function rather than a middleware hook because of a bug
    worth stating plainly. The first version did this in `after_agent` and
    returned `{"messages": [...]}` — which updates state but does **not**
    restart the agent loop, because `after_agent` runs when the agent is
    already finished. So the gate detected two fabricated quotes, logged
    "revising", and the run ended with them still in the memo.

    Detection worked and the fix never fired, which is the most dangerous shape
    a safety control can take: the logs say it acted. The loop belongs in the
    caller, which can actually invoke the agent again.
    """
    memo = workspace.read_memo()
    if not memo:
        return None, None
    report = verify_memo(memo, workspace)
    emit(
        {
            "event": "verification",
            "score": report.score,
            "passed": report.passed,
            "failures": len(report.failures),
            "attempt": attempt,
        }
    )
    if report.passed:
        return report, None
    return report, HumanMessage(findings_message(report, attempt))


class VerificationGateMiddleware(AgentMiddleware):
    """Reports the deterministic verdict as the agent finishes.

    Detection only. The revision loop lives in the caller — see
    :func:`revision_request` for why.
    """

    def __init__(self, workspace: RunWorkspace, *, max_revisions: int = MAX_REVISIONS) -> None:
        super().__init__()
        self.workspace = workspace
        self.max_revisions = max_revisions
        self.attempts = 0
        self.last_report: Any = None

    @property
    def name(self) -> str:
        return "VerificationGate"


__all__ = [
    "MAX_REVISIONS",
    "VerificationGateMiddleware",
    "findings_message",
    "revision_request",
]
