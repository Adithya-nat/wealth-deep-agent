"""Assembles the roster. The only module that knows about all of the agents.

Every other file in this package describes one agent and nothing else, which is
what makes them readable on a projector. This one answers the question they
each deliberately avoid: which agents exist, and which harness does each get?

    agent                   harness   why
    ---------------------   -------   --------------------------------------
    portfolio-analyst       deep      open-ended; offloads position data
    spend-analyst           deep      open-ended; six months of transactions
    market-researcher       deep      most open-ended; search, read, follow
    allocation-strategist   shallow   bounded; one right answer, typed output
    verifier                shallow   bounded; two checks, two tool calls

A subagent is a context window with a job, and the harness should follow from
the job rather than from habit.
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from deepagents import CompiledSubAgent, SubAgent

from wealth_agent.agents import (
    allocation_strategist,
    market_researcher,
    portfolio_analyst,
    spend_analyst,
    verifier,
)
from wealth_agent.data.store import GroundingLedger
from wealth_agent.models import RunMeter

#: Agent names, for the live panel and for trace filtering.
PORTFOLIO_ANALYST = portfolio_analyst.NAME
SPEND_ANALYST = spend_analyst.NAME
MARKET_RESEARCHER = market_researcher.NAME
ALLOCATION_STRATEGIST = allocation_strategist.NAME
VERIFIER = verifier.NAME

DEEP_AGENTS = (PORTFOLIO_ANALYST, SPEND_ANALYST, MARKET_RESEARCHER)


def build_analyst_subagents(
    *,
    portfolio_tools: list[BaseTool],
    spend_tools: list[BaseTool],
    research_tools: list[BaseTool],
    model: str | BaseChatModel,
    ledger: GroundingLedger,
    researcher_model: str | BaseChatModel | None = None,
    analyst_model: str | BaseChatModel | None = None,
    meter: RunMeter | None = None,
) -> list[SubAgent]:
    """The three deep subagents.

    Args:
        portfolio_tools: Built by ``build_portfolio_tools``.
        spend_tools: Built by ``build_spend_tools``.
        research_tools: Built by ``build_research_tools``.
        model: Default model for any agent without an override.
        ledger: The run's grounding ledger. Each subagent gets its own
            recording middleware — see ``common.subagent_middleware``.
        researcher_model: Override for the researcher. The workshop's "weak
            researcher" configuration uses it to reproduce citation drift.
        analyst_model: Override for the portfolio and spend analysts. They call
            a handful of deterministic tools and write a paragraph, so they run
            on a smaller model than the supervisor.
    """
    analyst = analyst_model or model
    return [
        portfolio_analyst.build(
            tools=portfolio_tools, model=analyst, ledger=ledger, meter=meter
        ),
        spend_analyst.build(tools=spend_tools, model=analyst, ledger=ledger, meter=meter),
        market_researcher.build(
            tools=research_tools,
            model=researcher_model or model,
            ledger=ledger,
            meter=meter,
        ),
    ]


def build_allocation_strategist(
    allocation_tools: list[BaseTool],
    model: str | BaseChatModel,
    ledger: GroundingLedger,
    meter: RunMeter | None = None,
) -> CompiledSubAgent:
    """The strategist. Also plain ReAct — see ``allocation_strategist.py``."""
    return allocation_strategist.build(
        tools=allocation_tools, model=model, ledger=ledger, meter=meter
    )


def build_verifier_subagent(
    verification_tools: list[BaseTool],
    model: str | BaseChatModel,
    meter: RunMeter | None = None,
) -> CompiledSubAgent:
    """The verifier. Plain ReAct, not a deep agent — see ``verifier.py``."""
    return verifier.build(tools=verification_tools, model=model, meter=meter)


__all__ = [
    "ALLOCATION_STRATEGIST",
    "DEEP_AGENTS",
    "MARKET_RESEARCHER",
    "PORTFOLIO_ANALYST",
    "SPEND_ANALYST",
    "VERIFIER",
    "build_allocation_strategist",
    "build_analyst_subagents",
    "build_verifier_subagent",
]
