"""Tests for the cost model.

The accounting is worth testing for an unusual reason: when it is wrong it is
wrong *quietly*. A meter that reports zero cache reads looks exactly like a
cache that is not working, and a meter that double-counts cached tokens as
fresh input reports a bill that is too high and sends you optimizing something
that is already fine.
"""

from __future__ import annotations

import pytest

from wealth_agent.models import (
    MIN_CACHEABLE_TOKENS,
    PRICES,
    REASONING_MODEL,
    WORKING_MODEL,
    RunMeter,
    build_model,
)


def usage(input_tokens: int, output: int, cache_read: int = 0, cache_write: int = 0) -> dict:
    """Shaped like `AIMessage.usage_metadata` from langchain-anthropic.

    `input_tokens` is reported *inclusive* of the cached portion, which is the
    detail the meter has to get right.
    """
    return {
        "input_tokens": input_tokens,
        "output_tokens": output,
        "input_token_details": {"cache_read": cache_read, "cache_creation": cache_write},
    }


def test_cached_tokens_are_not_also_billed_as_fresh_input() -> None:
    meter = RunMeter()
    meter.record("supervisor", REASONING_MODEL, usage(10_000, 500, cache_read=9_000))
    entry = meter.by_agent["supervisor"]
    assert entry.cache_read == 9_000
    assert entry.input_tokens == 1_000, "cached tokens were double-counted as fresh input"


def test_cost_uses_the_model_each_agent_actually_ran() -> None:
    """Tiering only saves money if the accounting prices each agent separately."""
    meter = RunMeter()
    meter.record("supervisor", REASONING_MODEL, usage(1_000_000, 0))
    meter.record("spend-analyst", WORKING_MODEL, usage(1_000_000, 0))
    assert meter.cost("supervisor") == pytest.approx(PRICES[REASONING_MODEL]["input"])
    assert meter.cost("spend-analyst") == pytest.approx(PRICES[WORKING_MODEL]["input"])
    assert meter.cost("spend-analyst") < meter.cost("supervisor")


def test_cache_hit_rate_is_zero_when_nothing_is_cached() -> None:
    """The number that tells you caching silently is not working."""
    meter = RunMeter()
    meter.record("supervisor", REASONING_MODEL, usage(10_000, 100))
    assert meter.cache_hit_rate == 0.0


def test_cache_hit_rate_reaches_one_when_everything_is_cached() -> None:
    meter = RunMeter()
    meter.record("supervisor", REASONING_MODEL, usage(10_000, 100, cache_read=10_000))
    assert meter.cache_hit_rate == 1.0


def test_a_cache_write_is_not_lost_into_the_full_price_bucket() -> None:
    """Some responses report the write only in the ephemeral buckets."""
    meter = RunMeter()
    meter.record(
        "supervisor",
        REASONING_MODEL,
        {
            "input_tokens": 5_000,
            "output_tokens": 10,
            "input_token_details": {
                "cache_read": 0,
                "cache_creation": 0,
                "ephemeral_5m_input_tokens": 4_800,
                "ephemeral_1h_input_tokens": 0,
            },
        },
    )
    assert meter.by_agent["supervisor"].cache_write == 4_800


def test_an_unpriced_model_reports_zero_rather_than_guessing() -> None:
    meter = RunMeter()
    meter.record("supervisor", "some-future-model", usage(1_000_000, 1_000_000))
    assert meter.cost() == 0.0
    assert meter.total_tokens == 2_000_000, "tokens are still counted"


def test_caching_is_on_by_default_and_can_be_turned_off() -> None:
    assert build_model(WORKING_MODEL).model_kwargs.get("cache_control") == {"type": "ephemeral"}
    assert "cache_control" not in build_model(WORKING_MODEL, cache=False).model_kwargs


def test_cache_thresholds_are_documented_for_every_priced_model() -> None:
    """Measured against this account, 2026-08.

    haiku-4-5 did NOT cache a 3,916-token prefix and DID cache 5,216 — four
    times sonnet's floor, on the model you reach for precisely to save money.
    An agent whose prefix does not clear its threshold gets no caching at all,
    silently, so every priced model needs a documented number here.
    """
    for model in PRICES:
        assert model in MIN_CACHEABLE_TOKENS, f"{model} has no documented cache floor"
    assert MIN_CACHEABLE_TOKENS[WORKING_MODEL] > MIN_CACHEABLE_TOKENS[REASONING_MODEL]


# --------------------------------------------------------------------------
# Tiering is a judgement about the job, not a blanket cost cut
# --------------------------------------------------------------------------


def test_tiering_is_documented_per_agent_in_the_supervisor() -> None:
    """Guard against a future "let's save money" pass demoting the strategist.

    Tiering is only free where a smaller model reaches the same answer — true
    for agents that call deterministic tools and report what they said, false
    for the one agent producing a judgement a human acts on with money.
    """
    import inspect

    from wealth_agent.agents import supervisor

    source = inspect.getsource(supervisor.build_wealth_agent)
    assert "strategist_model = model" in source, (
        "the allocation strategist must run on the reasoning model — its output "
        "is the recommendation a human acts on"
    )
    assert "analyst_model = build_model(WORKING_MODEL)" in source
