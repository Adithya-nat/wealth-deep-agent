"""The verifier's tools. Deterministic, cheap, and the same code the evals run.

One implementation serves three callers:

* the **verifier subagent**, when the supervisor delegates a check,
* **`RubricMiddleware`**, as evidence for the runtime grader,
* the **offline evaluator**, scoring an experiment.

That is deliberate. If the runtime check and the eval check are different code,
they will drift, and the day they disagree you will not know which one to
believe. One function, three entry points.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool

from wealth_agent.store import RunWorkspace
from wealth_agent.verify import verify_memo


def build_verification_tools(ws: RunWorkspace) -> list[BaseTool]:
    """Build verification tools bound to one run's evidence."""

    @tool
    def verify_report(memo: str) -> dict[str, Any]:
        """Check every citation and figure in a memo against recorded evidence.

        Runs no model. Each citation is checked for whether the source was
        actually fetched and whether any quoted span really appears in it; each
        figure is checked against every number observed in a tool result or a
        fetched page.

        Verdicts are `grounded`, `unsupported` (nothing recorded supports it —
        it may still be true), or `fabricated` (cites a source that does not
        exist, or quotes text that is not in it).

        Args:
            memo: The full markdown memo to check.
        """
        return verify_memo(memo, ws).to_json()

    @tool
    def evidence_summary() -> dict[str, Any]:
        """Summarize what evidence this run has recorded so far.

        Useful before writing: it tells you what you are actually able to cite.
        """
        described = ws.describe()
        return {
            **described,
            "source_ids": sorted(ws.sources()),
            "distinct_values_observed": len(ws.grounded_values()),
        }

    return [verify_report, evidence_summary]


__all__ = ["build_verification_tools"]
