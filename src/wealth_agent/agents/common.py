"""What every agent in this package needs, in one place.

`transient_errors` and `subagent_middleware` used to live in `supervisor.py`,
which meant `subagents.py` imported from its own importer and had to do it
inside a function body to dodge the cycle. Pulling both here removes the cycle
rather than working around it.
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
)

from wealth_agent.data.store import GroundingLedger
from wealth_agent.middleware.grounding_ledger import GroundingLedgerMiddleware
from wealth_agent.models import CostMeterMiddleware, RunMeter

#: Runaway guards. **Not budgets.**
#:
#: The distinction is the whole lesson, and I learned it by getting it wrong.
#: The first version set the supervisor to 30 because a healthy run used about
#: 30. A run then hit exactly 30, `exit_behavior="end"` stopped the agent
#: mid-sentence, and the CLI printed a grounding score and a report path as
#: though nothing had happened. The memo ended at an empty `## Portfolio`
#: heading. Six of seven todos were done. Nothing errored.
#:
#: That is the same failure this module's own `transient_errors` docstring
#: warns about — a limit that converts a loud failure into a quiet wrong
#: answer — reintroduced by the mechanism meant to make the run safe.
#:
#: Two rules came out of it:
#:
#: 1. **A ceiling that binds during normal operation is set wrong.** These are
#:    roughly 3x observed healthy usage (measured: supervisor 30, researcher
#:    18, analysts 4, strategist 4). They exist to stop a loop that will never
#:    terminate, not to shape a run that will.
#: 2. **Hitting one is a defect, not a degradation.** `truncated_agents()`
#:    below detects it and the CLI and the report both refuse to present a
#:    truncated memo as finished. An unbounded agent is a bad idea; an agent
#:    that quietly returns half its work is a worse one.
CALL_LIMITS: dict[str, int] = {
    "supervisor": 100,
    "portfolio-analyst": 40,
    "spend-analyst": 40,
    "market-researcher": 50,
    "allocation-strategist": 20,
    "verifier": 25,
}


def truncated_agents(meter: Any) -> list[str]:
    """Agents that reached their ceiling, and were therefore cut short.

    Call-count equality is the signal available after the fact:
    `ModelCallLimitMiddleware` ends the agent rather than raising, so there is
    nothing to catch. Any agent here means the run is incomplete and must be
    reported that way rather than scored as though it finished.
    """
    return [
        name
        for name, usage in getattr(meter, "by_agent", {}).items()
        if usage.calls >= CALL_LIMITS.get(name, 10**6)
    ]


def transient_errors() -> tuple[type[Exception], ...]:
    """Exception types worth retrying, and only those.

    Retrying on bare `Exception` is a trap this repo fell into. A run hit
    `BadRequestError: your credit balance is too low`, the middleware retried it
    five times, gave up, and — with the default `on_failure="continue"` —
    handed the error *string* back to the agent as if it were a model response.
    The agent dutifully wrote it into the memo. Twelve experiment runs reported
    `success`, every score came back 0.0, and nothing anywhere said "you are out
    of credits."

    A billing error, a bad API key, and a malformed request are all permanent.
    Retrying them wastes time and, worse, converts a loud failure into a quiet
    wrong answer. Connection drops and rate limits are the retryable ones.
    """
    try:
        from anthropic import (
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
            RateLimitError,
        )
    except ImportError:  # pragma: no cover
        return (ConnectionError, TimeoutError)
    return (
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        RateLimitError,
        ConnectionError,
        TimeoutError,
    )


def retry_middleware() -> ModelRetryMiddleware:
    """Retry only what is worth retrying.

    A long run makes hundreds of model calls across five agents. Over several
    minutes the probability that *none* of them hits a transient connection
    error is not close to one — and without a retry, one dropped socket near the
    end discards the whole run and everything it cost.
    """
    return ModelRetryMiddleware(
        max_retries=4,
        initial_delay=2.0,
        max_delay=30.0,
        retry_on=transient_errors(),
        on_failure="error",
    )


def call_limit(name: str) -> ModelCallLimitMiddleware:
    """A hard ceiling on model calls for one agent, per run."""
    return ModelCallLimitMiddleware(run_limit=CALL_LIMITS.get(name, 20), exit_behavior="end")


def subagent_middleware(
    ledger: GroundingLedger, *, name: str, meter: RunMeter | None = None
) -> list[AgentMiddleware]:
    """The middleware every deep subagent needs.

    All of these are cross-cutting concerns, and all have to be installed
    **per subagent** — declarative subagents are compiled with their own stack
    and inherit nothing from the parent. Centralizing them here rather than
    repeating the list in each agent file means adding a sixth agent cannot
    accidentally opt out of either.

    We shipped the bug this prevents, and it was invisible in the nicest
    possible way: the recording middleware was installed on the supervisor only,
    so every tool call made *inside* a subagent never reached the ledger.
    Nothing errored. The agent ran beautifully. The verifier then reported
    `$18,420.55` — the actual cash balance, straight from `get_account_balances`
    — as unsupported, because the evidence had been destroyed at the boundary
    between two context windows. Which is the exact failure this whole system
    exists to catch. It caught it. On itself.

    The lesson generalizes past the bug: **in a multi-agent system, cross-cutting
    concerns have to be installed per agent.** Logging, redaction, rate limiting,
    and audit trails all have this shape, and all of them fail silently the
    same way.
    """
    middleware: list[AgentMiddleware] = [
        retry_middleware(),
        call_limit(name),
        GroundingLedgerMiddleware(ledger, agent_name=name),
    ]
    if meter is not None:
        middleware.append(CostMeterMiddleware(meter, agent_name=name))
    return middleware


__all__ = [
    "CALL_LIMITS",
    "call_limit",
    "truncated_agents",
    "retry_middleware",
    "subagent_middleware",
    "transient_errors",
]
