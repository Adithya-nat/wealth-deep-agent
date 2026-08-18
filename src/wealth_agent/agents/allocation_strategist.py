"""The allocation strategist — the second agent that is deliberately *not* deep.

The verifier established the principle; this one shows it was a principle and
not a one-off. Both jobs are bounded — call a handful of deterministic tools,
reach the one right answer, return it — so both get a plain `create_agent` ReAct
loop instead of a deep-agent harness they would not use.

What is different here is `response_format`. This agent's output is the part a
human acts on with money, so it comes back as a validated `RecommendationSet`
rather than prose the report would have to parse. The arithmetic already
happened in `tools/allocation.py`; what the model contributes is judgment about
*which* drifts matter and how to explain them, which is exactly the division of
labour this whole repo argues for.
"""

from __future__ import annotations

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from deepagents import CompiledSubAgent

from wealth_agent import prompts
from wealth_agent.agents.common import call_limit, retry_middleware
from wealth_agent.data.store import GroundingLedger
from wealth_agent.middleware.grounding_ledger import GroundingLedgerMiddleware
from wealth_agent.models import CostMeterMiddleware, RunMeter
from wealth_agent.recommendations import RecommendationSet

NAME = "allocation-strategist"

DESCRIPTION = (
    "Turns policy drift into specific, dollar-denominated recommended actions. "
    "Delegate after the portfolio and spend analysts have run — it needs both. "
    "Returns a structured recommendation set with the exact trade amounts."
)


def build(
    *,
    tools: list[BaseTool],
    model: str | BaseChatModel,
    ledger: GroundingLedger,
    meter: RunMeter | None = None,
) -> CompiledSubAgent:
    """Build the strategist.

    Note:
        It gets the grounding ledger like every other agent. That is not
        optional bookkeeping: without it the dollar amounts in the
        recommendations would appear in no recorded tool result, and the memo's
        most consequential figures would be the only unverifiable ones in it.
    """
    middleware = [
        retry_middleware(),
        call_limit(NAME),
        GroundingLedgerMiddleware(ledger, agent_name=NAME),
    ]
    if meter is not None:
        middleware.append(CostMeterMiddleware(meter, agent_name=NAME))

    runnable = create_agent(
        model=model,
        tools=tools,
        system_prompt=prompts.render("allocation_strategist"),
        response_format=RecommendationSet,
        middleware=middleware,
        name=NAME,
    )
    return CompiledSubAgent(name=NAME, description=DESCRIPTION, runnable=runnable)


__all__ = ["DESCRIPTION", "NAME", "build"]
