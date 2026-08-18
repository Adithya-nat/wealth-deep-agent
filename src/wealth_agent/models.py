"""Which model each agent gets, what it costs, and how to spend less.

Three decisions live here, and all three are the kind a customer asks about
before they ask about anything else.

**Caching.** Every agent in this system re-sends a large, byte-identical prefix
on every turn: the deep-agent harness prompt, the skills index, the tool
schemas, and the conversation so far. Across ~40 model calls in a run that is
the majority of the bill, and Anthropic will serve it from cache at a tenth of
the price. `cache_control` on the request turns that on. It is one dictionary
and it is the single largest saving in the repo.

**Tiering.** The supervisor synthesizes and the researcher reads the open web;
those are judgment. The portfolio and spend analysts call four deterministic
tools and write a paragraph, and the rubric grader reads a verdict off a tool's
output. Paying a frontier price for the second group buys nothing.

**Measurement.** `RunMeter` and `CostMeterMiddleware` count what actually
happened, per agent, and the number goes on the live panel and in the report
footer. The point is not the accounting — it is that "what does verification
cost?" becomes a number you can put on a slide next to what it caught, so the
room can have the argument on evidence instead of on vibes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_anthropic import ChatAnthropic

#: Anthropic list prices, US dollars per million tokens, as of 2026-08.
#:
#: `cache_read` is the interesting column: a tenth of the input price. That
#: ratio is why the caching decision above dominates every other lever.
#: `cache_write` is a 25% surcharge paid once per distinct prefix.
PRICES: dict[str, dict[str, float]] = {
    # Sonnet 5 is on introductory pricing ($2/$10) through 2026-08-31; list is
    # $3/$15. Using the intro numbers would quietly understate every estimate
    # in the report the day it lapses, so this table carries list price.
    "claude-sonnet-5": {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00, "cache_write": 1.25, "cache_read": 0.10},
    "claude-opus-5": {"input": 5.00, "output": 25.00, "cache_write": 6.25, "cache_read": 0.50},
}

#: The minimum prefix a request needs before Anthropic will cache it at all,
#: **measured against this account** rather than taken from documentation.
#:
#: This is the trap in the whole cost story. Below the threshold nothing errors,
#: nothing warns, and `cache_read` simply stays zero forever — you just quietly
#: pay full price on every turn. And the threshold is *four times higher* on
#: Haiku than on Sonnet, which is exactly backwards from where you would guess:
#: the cheap model you reach for to save money is the one that most easily
#: fails to cache.
#:
#: Measured (see `tests/test_models.py::test_cache_thresholds_are_documented`):
#:   sonnet-5    cached a 1,671-token prefix
#:   haiku-4-5   did NOT cache 3,916 tokens; DID cache 5,216
#:
#: The practical consequence for this repo: an agent is only worth putting on
#: Haiku if its system prefix clears 4k. `wealth doctor` checks this per agent
#: so the answer is measured rather than assumed.
MIN_CACHEABLE_TOKENS: dict[str, int] = {
    "claude-sonnet-5": 1024,
    "claude-sonnet-4-6": 1024,
    "claude-opus-5": 1024,
    "claude-haiku-4-5": 4096,
}

#: The model that reasons: plans the review, synthesizes the memo, reads the
#: open web and decides what matters in it.
REASONING_MODEL = "claude-sonnet-5"

#: The model that executes a bounded job: call some deterministic tools, report
#: what they said. Both analysts, the strategist, and the rubric grader.
WORKING_MODEL = "claude-haiku-4-5"

#: A deliberately weakened researcher, used by the workshop to make citation
#: drift reproducible. Compressing six sources into one summary is where
#: attribution gets lost, and a smaller model loses it every run instead of one
#: run in twelve. The honest version of "here is a bug" is "here is a bug I can
#: reproduce."
WEAK_RESEARCHER_MODEL = "claude-haiku-4-5"


def build_model(model: str = REASONING_MODEL, *, cache: bool = True, **kwargs: Any) -> ChatAnthropic:
    """Build a chat model, cached by default.

    Args:
        model: A bare Anthropic model id, e.g. `claude-sonnet-5`. No
            `anthropic:` prefix — we construct the client rather than letting
            the harness resolve a string, because a string cannot carry the
            caching configuration below.
        cache: Request automatic prompt caching. Anthropic places the
            breakpoint on the last cacheable block, so the whole stable prefix
            — harness prompt, skills, tool schemas, prior turns — is served
            from cache on every turn after the first.

    Note:
        Caching fails *silently* in two ways, and neither raises. A prefix
        shorter than :data:`MIN_CACHEABLE_TOKENS` is never cached at all; and a
        prefix that changes byte-for-byte between turns — a timestamp in a
        system prompt, a tool list that reorders, a UUID — invalidates
        everything after it. In both cases the bill simply does not go down.

        That is why `CostMeterMiddleware` tracks cache reads separately and
        `wealth cost` prints the hit rate. An unmeasured cache is an
        assumption, not a saving.
    """
    if cache:
        kwargs.setdefault("model_kwargs", {})["cache_control"] = {"type": "ephemeral"}
    return ChatAnthropic(model=model, **kwargs)


@dataclass
class AgentUsage:
    """What one agent spent."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read + self.cache_write


@dataclass
class RunMeter:
    """Token and dollar accounting for one run, broken down by agent.

    Shared by reference across every agent's middleware, so the total is live
    while the run is still going. That is what lets the panel show a dollar
    figure that moves.
    """

    by_agent: dict[str, AgentUsage] = field(default_factory=dict)
    models: dict[str, str] = field(default_factory=dict)

    def record(self, agent: str, model: str, usage: dict[str, Any]) -> None:
        entry = self.by_agent.setdefault(agent, AgentUsage())
        details = usage.get("input_token_details") or {}
        cache_read = int(details.get("cache_read") or 0)
        # `cache_creation` is reported as 0 on some responses that did write a
        # cache entry, with the real figure split across the ephemeral buckets.
        # Taking the max of the three keeps the write from vanishing into the
        # full-price input bucket and overstating the bill.
        cache_write = max(
            int(details.get("cache_creation") or 0),
            int(details.get("ephemeral_5m_input_tokens") or 0)
            + int(details.get("ephemeral_1h_input_tokens") or 0),
        )
        entry.calls += 1
        entry.output_tokens += int(usage.get("output_tokens") or 0)
        entry.cache_read += cache_read
        entry.cache_write += cache_write
        # LangChain reports `input_tokens` inclusive of the cached portion.
        # Subtracting keeps the three buckets disjoint so they can be priced
        # separately and still sum to the truth.
        entry.input_tokens += max(0, int(usage.get("input_tokens") or 0) - cache_read - cache_write)
        self.models[agent] = model

    def cost(self, agent: str | None = None) -> float:
        """Dollars, priced per agent against the model that agent actually ran."""
        agents = [agent] if agent else list(self.by_agent)
        total = 0.0
        for name in agents:
            usage = self.by_agent.get(name)
            if usage is None:
                continue
            price = PRICES.get(_base_model(self.models.get(name, "")))
            if price is None:  # an unpriced model is reported as zero, not guessed at
                continue
            total += (
                usage.input_tokens * price["input"]
                + usage.output_tokens * price["output"]
                + usage.cache_read * price["cache_read"]
                + usage.cache_write * price["cache_write"]
            ) / 1_000_000
        return round(total, 4)

    @property
    def total_tokens(self) -> int:
        return sum(u.total_tokens for u in self.by_agent.values())

    @property
    def cache_hit_rate(self) -> float:
        """Share of input tokens served from cache, in ``[0, 1]``.

        Near zero on a multi-turn run means caching is not working. Check this
        before believing any cost number in this repo.
        """
        cached = sum(u.cache_read for u in self.by_agent.values())
        fresh = sum(u.input_tokens + u.cache_write for u in self.by_agent.values())
        return round(cached / (cached + fresh), 4) if cached + fresh else 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "total_tokens": self.total_tokens,
            "total_cost_usd": self.cost(),
            "cache_hit_rate": self.cache_hit_rate,
            "by_agent": {
                name: {
                    "model": self.models.get(name, "unknown"),
                    "calls": u.calls,
                    "input_tokens": u.input_tokens,
                    "output_tokens": u.output_tokens,
                    "cache_read": u.cache_read,
                    "cache_write": u.cache_write,
                    "cost_usd": self.cost(name),
                }
                for name, u in sorted(self.by_agent.items())
            },
        }


def _base_model(model: str) -> str:
    """Strip a provider prefix and a date suffix so the price table matches."""
    name = model.split(":", 1)[-1]
    for known in PRICES:
        if name.startswith(known):
            return known
    return name


class CostMeterMiddleware(AgentMiddleware):
    """Records what each model call cost, and streams it out as it happens.

    Installed per agent for the same reason the grounding ledger is: declarative
    subagents compile their own middleware stack and inherit nothing, so an
    accountant on the supervisor alone would report a fraction of the bill and
    look plausible doing it.
    """

    def __init__(self, meter: RunMeter, *, agent_name: str) -> None:
        super().__init__()
        self.meter = meter
        self.agent_name = agent_name

    @property
    def name(self) -> str:
        return f"CostMeter[{self.agent_name}]"

    def _record(self, response: Any, request: ModelRequest) -> None:
        message = getattr(response, "result", None)
        message = message[-1] if isinstance(message, list) and message else message
        usage = getattr(message, "usage_metadata", None)
        if not usage:
            return
        model = getattr(request.model, "model", None) or str(request.model)
        self.meter.record(self.agent_name, str(model), dict(usage))
        _emit(self.meter)

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        response = handler(request)
        self._record(response, request)
        return response

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        response = await handler(request)
        self._record(response, request)
        return response


def _emit(meter: RunMeter) -> None:
    """Push the running total to the stream. See `middleware/events.py`."""
    from wealth_agent.middleware.events import emit

    emit(
        {
            "event": "cost",
            "tokens": meter.total_tokens,
            "cost_usd": meter.cost(),
            "cache_hit_rate": meter.cache_hit_rate,
        }
    )


__all__ = [
    "MIN_CACHEABLE_TOKENS",
    "PRICES",
    "REASONING_MODEL",
    "WEAK_RESEARCHER_MODEL",
    "WORKING_MODEL",
    "AgentUsage",
    "CostMeterMiddleware",
    "RunMeter",
    "build_model",
]
