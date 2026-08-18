"""The portfolio analyst — a deep agent, because its work is open-ended.

It decides what to look at next based on what it just found, and offloads the
position data to the filesystem so the supervisor never has to hold it.
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from deepagents import SubAgent

from wealth_agent import prompts
from wealth_agent.agents.common import subagent_middleware
from wealth_agent.models import RunMeter
from wealth_agent.data.store import PORTFOLIO_DIR, GroundingLedger

NAME = "portfolio-analyst"

DESCRIPTION = (
    "Analyzes holdings: positions, balances, concentration, sector exposure, "
    "and unrealized P/L. Delegate anything about what the person owns or how "
    "their portfolio is allocated."
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
        system_prompt=prompts.render("portfolio_analyst", PORTFOLIO_DIR=PORTFOLIO_DIR),
        tools=tools,
        model=model,
        middleware=subagent_middleware(ledger, name=NAME, meter=meter),
    )


__all__ = ["DESCRIPTION", "NAME", "build"]
