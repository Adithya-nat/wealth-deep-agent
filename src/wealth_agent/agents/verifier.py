"""The verifier — deliberately **not** a deep agent.

Its job has exactly one right answer and takes two tool calls to reach. A
deep-agent harness would hand it a filesystem it will not use, a `task` tool it
must not use, planning it does not need, and latency on every run.

Making that choice explicitly — rather than reaching for `create_deep_agent` a
fourth time because it is there — is most of what "architecture" means in an
agent system. The reflex to give every component the most capable harness
available is how these systems get slow and unpredictable.

See `allocation_strategist.py` for the second instance of the same call.
"""

from __future__ import annotations

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from deepagents import CompiledSubAgent

from wealth_agent import prompts
from wealth_agent.agents.common import call_limit, retry_middleware
from wealth_agent.models import CostMeterMiddleware, RunMeter

NAME = "verifier"

DESCRIPTION = (
    "Checks a finished memo against the evidence recorded this run and reports "
    "every citation or figure that does not hold up. Call this before "
    "returning any memo to the user."
)


def build(
    *, tools: list[BaseTool], model: str | BaseChatModel, meter: RunMeter | None = None
) -> CompiledSubAgent:
    middleware = [retry_middleware(), call_limit(NAME)]
    if meter is not None:
        middleware.append(CostMeterMiddleware(meter, agent_name=NAME))
    runnable = create_agent(
        model=model,
        tools=tools,
        system_prompt=prompts.render("verifier"),
        middleware=middleware,
        name=NAME,
    )
    return CompiledSubAgent(name=NAME, description=DESCRIPTION, runnable=runnable)


__all__ = ["DESCRIPTION", "NAME", "build"]
