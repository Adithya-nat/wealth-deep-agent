"""The market researcher — the most open-ended of the deep agents.

Search, read, follow a lead, read again. It needs its own context to burn and
its own scratch space, which is exactly what a deep-agent harness provides.

The `weak` model option is a workshop device, not an accident. Compressing six
sources into one summary is where attribution gets lost, and a smaller model
loses it every run instead of one run in twelve — so the failure the verifier
exists to catch becomes reproducible on demand. The honest version of "here is
a bug" is "here is a bug I can reproduce."
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from deepagents import SubAgent

from wealth_agent import prompts
from langchain.agents.middleware import ToolCallLimitMiddleware

from wealth_agent.agents.common import subagent_middleware
from wealth_agent.models import RunMeter
from wealth_agent.data.store import SOURCES_DIR, GroundingLedger

#: How many pages the researcher may fetch in one run.
#:
#: Research is the one job here with no natural stopping point — there is always
#: another article — so it gets an explicit one. Six pages is enough to cover
#: five recommended instruments with a spare, and the cap is what keeps "read
#: the web about this" from being an open-ended charge on the run.
#:
#: `exit_behavior="continue"` rather than ending the agent: hitting the cap
#: should stop it fetching, not throw away the sources it already has.
MAX_PAGE_FETCHES = 12

NAME = "market-researcher"

DESCRIPTION = (
    "Researches the web for external context on named instruments, sectors, or "
    "markets, and stores every page it reads as a citable source. Delegate "
    "anything requiring outside information, naming the instruments to cover."
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
        system_prompt=prompts.render("market_researcher", SOURCES_DIR=SOURCES_DIR),
        tools=tools,
        model=model,
        middleware=[
            *subagent_middleware(ledger, name=NAME, meter=meter),
            ToolCallLimitMiddleware(
                tool_name="fetch_page",
                run_limit=MAX_PAGE_FETCHES,
                exit_behavior="continue",
            ),
        ],
    )


__all__ = ["DESCRIPTION", "MAX_PAGE_FETCHES", "NAME", "build"]
