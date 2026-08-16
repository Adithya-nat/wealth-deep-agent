"""Loop 2: offline experiments against a LangSmith dataset.

Loop 1 (`RubricMiddleware`) protects one run. Loop 2 measures the *system*: run
the same questions through two configurations and compare. That is the only way
to answer "did that change help?", which is the question every agent team is
actually asking and almost none can answer with a number.

Two datasets live here and they do different jobs:

* **memo questions** — the agent runs for real, and the evaluators score what it
  produced. Slow, expensive, closest to production.
* **judge fixtures** — pre-written memos with human labels. No agent runs. This
  is the dataset for Loop 0, and it is cheap enough to run on every prompt edit.

The evaluators reopen each run's workspace by id rather than being handed live
objects. It makes them reusable against runs that finished hours ago, which is
what you want when a regression shows up and the interesting run is yesterday's.
"""

from __future__ import annotations

import asyncio
from typing import Any

from wealth_agent.evals.evaluators import (
    covers_required_sections,
    deterministic_grounding,
    no_fabrications,
)
from wealth_agent.evals.fixtures import build_fixtures
from wealth_agent.store import RunWorkspace

MEMO_DATASET = "wealth-agent-memo-questions"
JUDGE_DATASET = "wealth-agent-judge-fixtures"

#: The questions the experiment runs. Chosen to exercise different paths —
#: portfolio only, spend only, both, and one that requires external research
#: (the path where citation drift lives).
QUESTIONS: tuple[tuple[str, str], ...] = (
    ("q1-allocation", "How is my portfolio allocated, and is it concentrated?"),
    ("q2-spend", "Where did my money go over the last three months?"),
    ("q3-subscriptions", "What am I paying for on a recurring basis, and what does it cost me a year?"),
    ("q4-full-review", "Give me a full wealth review: allocation, spending, and market context."),
    ("q5-largest-holding", "Tell me about my largest holding, including any recent news about it."),
    ("q6-losers", "Which positions are down, and is there any market context explaining it?"),
)


def _client() -> Any:
    from langsmith import Client

    return Client()


# --------------------------------------------------------------------------
# Datasets
# --------------------------------------------------------------------------


def push_memo_dataset() -> str:
    """Create or update the memo-question dataset. Returns its name."""
    client = _client()
    if not client.has_dataset(dataset_name=MEMO_DATASET):
        dataset = client.create_dataset(
            dataset_name=MEMO_DATASET,
            description=(
                "Questions a wealth analyst agent should answer with a grounded, "
                "cited memo. Reference outputs are intentionally absent: there is "
                "no single correct memo, only memos whose claims hold up."
            ),
        )
    else:
        dataset = client.read_dataset(dataset_name=MEMO_DATASET)

    existing = {e.metadata.get("qid") for e in client.list_examples(dataset_id=dataset.id)}
    new = [(qid, q) for qid, q in QUESTIONS if qid not in existing]
    if new:
        client.create_examples(
            dataset_id=dataset.id,
            inputs=[{"question": q} for _, q in new],
            metadata=[{"qid": qid} for qid, _ in new],
        )
    return MEMO_DATASET


def push_judge_dataset() -> str:
    """Create or update the labeled judge-alignment dataset.

    Uploading the human `note` alongside the label matters: when the judge
    disagrees six months from now, the note is the only record of *why* a human
    called it that way. A dataset of bare 0s and 1s rots.
    """
    client = _client()
    if not client.has_dataset(dataset_name=JUDGE_DATASET):
        dataset = client.create_dataset(
            dataset_name=JUDGE_DATASET,
            description=(
                "Memos with human groundedness labels, used to measure whether an "
                "LLM-as-judge evaluator agrees with human reviewers."
            ),
        )
    else:
        dataset = client.read_dataset(dataset_name=JUDGE_DATASET)

    existing = {e.metadata.get("fixture_id") for e in client.list_examples(dataset_id=dataset.id)}
    fixtures = [f for f in build_fixtures() if f.id not in existing]
    if fixtures:
        client.create_examples(
            dataset_id=dataset.id,
            inputs=[{"memo": f.memo} for f in fixtures],
            outputs=[{"grounded": f.grounded, "rationale": f.note} for f in fixtures],
            metadata=[{"fixture_id": f.id, "defect": f.defect} for f in fixtures],
        )
    return JUDGE_DATASET


# --------------------------------------------------------------------------
# Experiment
# --------------------------------------------------------------------------


def _evaluators() -> list[Any]:
    """Evaluators that reopen a run's workspace from the id in the output."""

    def _workspace(outputs: dict[str, Any]) -> RunWorkspace | None:
        run_id = outputs.get("run_id")
        return RunWorkspace(run_id=run_id) if run_id else None

    def grounding(outputs: dict[str, Any]) -> dict[str, Any]:
        ws = _workspace(outputs)
        if ws is None:
            return {"key": "grounding", "score": 0.0, "comment": "no workspace"}
        return deterministic_grounding(outputs.get("memo", ""), ws)

    def fabrications(outputs: dict[str, Any]) -> dict[str, Any]:
        ws = _workspace(outputs)
        if ws is None:
            return {"key": "no_fabrications", "score": 0.0, "comment": "no workspace"}
        return no_fabrications(outputs.get("memo", ""), ws)

    def coverage(outputs: dict[str, Any]) -> dict[str, Any]:
        return covers_required_sections(outputs.get("memo", ""))

    return [grounding, fabrications, coverage]


async def _run_agent(question: str, *, mode: str, weak_researcher: bool) -> dict[str, Any]:
    from langchain_core.messages import HumanMessage

    from wealth_agent.cli import _resolve_memo
    from wealth_agent.supervisor import RUBRIC, build_wealth_agent

    bundle = await build_wealth_agent(mode=mode, weak_researcher=weak_researcher)
    payload: dict[str, Any] = {"messages": [HumanMessage(question)]}
    if bundle.verified:
        payload["rubric"] = RUBRIC
    result = await bundle.agent.ainvoke(
        payload,
        config={"configurable": {"thread_id": bundle.workspace.run_id}},
        recursion_limit=150,
    )
    memo = _resolve_memo(bundle.workspace, result)
    return {
        "memo": memo,
        "run_id": bundle.workspace.run_id,
        "mode": mode,
        "tool_calls": sum(
            len(getattr(m, "tool_calls", None) or []) for m in result.get("messages", [])
        ),
    }


def run_memo_experiment(
    *,
    mode: str,
    weak_researcher: bool = False,
    max_concurrency: int = 2,
) -> Any:
    """Run every dataset question through one configuration and score it.

    Args:
        mode: ``naive``, ``baseline``, or ``verified``.
        weak_researcher: Run the researcher on a smaller model.
        max_concurrency: Parallel agent runs. Kept low — each run spawns
            subagents and MCP subprocesses, and saturating the model provider
            just converts throughput into rate-limit errors.

    Returns:
        The LangSmith experiment results.
    """
    client = _client()
    dataset = push_memo_dataset()
    suffix = mode + ("-weak" if weak_researcher else "")

    def target(inputs: dict[str, Any]) -> dict[str, Any]:
        return asyncio.run(
            _run_agent(inputs["question"], mode=mode, weak_researcher=weak_researcher)
        )

    return client.evaluate(
        target,
        data=dataset,
        evaluators=_evaluators(),
        experiment_prefix=f"wealth-{suffix}",
        max_concurrency=max_concurrency,
        metadata={"mode": mode, "weak_researcher": weak_researcher},
    )


__all__ = [
    "JUDGE_DATASET",
    "MEMO_DATASET",
    "QUESTIONS",
    "push_judge_dataset",
    "push_memo_dataset",
    "run_memo_experiment",
]
