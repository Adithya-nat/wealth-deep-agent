"""The spend analyst — a deep agent, same shape as the portfolio analyst.

Six months of transactions, read through tools that keep the rows on disk. It
reads the category rulebook skill on demand rather than carrying it.
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from deepagents import SubAgent

from wealth_agent import prompts
from wealth_agent.agents.common import subagent_middleware
from wealth_agent.models import RunMeter
from wealth_agent.data.store import SPEND_DIR, GroundingLedger

NAME = "spend-analyst"

DESCRIPTION = (
    "Analyzes card spending: totals by category and merchant, monthly trends, "
    "recurring subscriptions, and period comparisons. Delegate anything about "
    "where money went."
)


def build(
    *,
    tools: list[BaseTool],
    model: str | BaseChatModel,
    ledger: GroundingLedger,
    meter: RunMeter | None = None,
) -> SubAgent:
    return SubAgent(
        name=NAME,
        description=DESCRIPTION,
        system_prompt=prompts.render("spend_analyst", SPEND_DIR=SPEND_DIR),
        tools=tools,
        model=model,
        middleware=subagent_middleware(ledger, name=NAME, meter=meter),
    )


__all__ = ["DESCRIPTION", "NAME", "build"]
