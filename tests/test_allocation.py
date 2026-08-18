"""Tests for the recommendation arithmetic.

Everything here is deterministic on purpose: these are the numbers a human will
act on with money, so they are the last place in the system where a model
should be involved. That makes them ordinary code, and ordinary code gets
ordinary tests.
"""

from __future__ import annotations

import json

import pytest

from wealth_agent.config import ARTIFACTS_DIR
from wealth_agent.data.store import PORTFOLIO_DIR, RunWorkspace
from wealth_agent.policy import Policy, PolicyError, load_policy
from wealth_agent.tools.allocation import build_allocation_tools


@pytest.fixture
def tools():
    """Bound to the frozen `verified` artifact, so these numbers never move."""
    ws = RunWorkspace(run_id="verified", base=ARTIFACTS_DIR / "runs")
    policy_targets, drift_report, cash_runway, rebalance_plan = build_allocation_tools(ws)
    return {
        "policy": policy_targets,
        "drift": drift_report,
        "runway": cash_runway,
        "plan": rebalance_plan,
    }


# --------------------------------------------------------------------------
# Policy
# --------------------------------------------------------------------------


def test_the_shipped_policy_loads_and_sums_to_one_hundred() -> None:
    policy = load_policy()
    assert sum(policy.sector_targets.values()) == pytest.approx(100.0)
    assert sum(policy.asset_class_targets.values()) == pytest.approx(100.0)


def test_a_policy_that_does_not_sum_to_one_hundred_is_refused(tmp_path, monkeypatch) -> None:
    """A plan derived from broken targets is confidently wrong, which is worse
    than refusing to start."""
    import wealth_agent.policy as policy_module

    monkeypatch.setattr(policy_module, "POLICIES_DIR", tmp_path)
    (tmp_path / "broken.json").write_text(
        json.dumps(
            {
                "asset_class_targets_percent_of_total": {"equity": 85, "cash": 15},
                "sector_targets_percent_of_equity": {"Tech": 80, "Energy": 60},
            }
        )
    )
    with pytest.raises(PolicyError, match="140"):
        policy_module.load_policy.__wrapped__("broken")


def test_a_sector_the_policy_never_mentions_targets_zero() -> None:
    assert load_policy().target_for("Cryptocurrency") == 0.0


# --------------------------------------------------------------------------
# Drift
# --------------------------------------------------------------------------


def test_drift_states_both_denominators() -> None:
    """Sector weights are of equity; the single-name cap is of total. Reporting
    one as the other is the most common error in this analysis."""
    ws = RunWorkspace(run_id="verified", base=ARTIFACTS_DIR / "runs")
    *_, _ = build_allocation_tools(ws)
    _, drift_report, _, _ = build_allocation_tools(ws)
    report = drift_report.invoke({})
    assert report["sector_denominator"] == "equity_only"
    assert report["asset_class_denominator"] == "total_value_including_cash"


def test_sector_weights_sum_to_one_hundred_percent_of_equity(tools) -> None:
    held = [s for s in tools["drift"].invoke({})["sectors"] if s["market_value"] > 0]
    assert sum(s["current_percent_of_equity"] for s in held) == pytest.approx(100.0, abs=0.05)


def test_a_sector_inside_the_band_is_not_breached(tools) -> None:
    report = tools["drift"].invoke({})
    band = report["drift_band_percentage_points"]
    for sector in report["sectors"]:
        assert sector["breached"] == (abs(sector["drift_percentage_points"]) > band)


def test_an_unheld_sector_with_a_target_shows_as_full_drift(tools) -> None:
    """Owning no bonds against an 8% target is a finding, not a missing row."""
    fixed = next(
        s for s in tools["drift"].invoke({})["sectors"] if s["sector"] == "Fixed Income"
    )
    assert fixed["market_value"] == 0.0
    assert fixed["drift_percentage_points"] == pytest.approx(-8.0)
    assert fixed["breached"]


# --------------------------------------------------------------------------
# Cash runway
# --------------------------------------------------------------------------


def test_reserve_is_sized_from_full_months_only(tools) -> None:
    """A reserve sized off a half month of data looks precise and is not."""
    runway = tools["runway"].invoke({})
    assert runway["full_months_observed"] >= 1
    assert runway["reserve_required"] == pytest.approx(
        runway["average_full_month_spend"] * runway["reserve_months"]
    )


def test_deployable_cash_goes_negative_when_the_reserve_is_short(tools) -> None:
    """This fixture is under-reserved, and the plan has to respect that rather
    than treating the whole cash balance as investable."""
    runway = tools["runway"].invoke({})
    assert runway["deployable_cash"] < 0
    assert runway["months_of_cover_at_current_cash"] < runway["reserve_months"]


# --------------------------------------------------------------------------
# The plan
# --------------------------------------------------------------------------


def test_no_action_is_smaller_than_the_minimum_trade_size(tools) -> None:
    policy = load_policy()
    for action in tools["plan"].invoke({})["actions"]:
        if action["action"] == "HOLD":
            continue
        assert action["dollars"] >= policy.min_trade_usd


def test_buys_never_exceed_available_funding(tools) -> None:
    """The plan must not assume money that does not exist."""
    plan = tools["plan"].invoke({})
    bought = sum(a["dollars"] for a in plan["actions"] if a["action"] == "BUY")
    assert bought <= plan["total_funding_available"] + 0.01


def test_an_under_reserved_account_funds_buys_from_sales_not_cash(tools) -> None:
    """The interesting result: cash is short, so the bond purchase is paid for
    by the tech trim rather than by drawing the reserve further down."""
    plan = tools["plan"].invoke({})
    assert plan["deployable_cash"] < 0
    bought = sum(a["dollars"] for a in plan["actions"] if a["action"] == "BUY")
    assert bought <= plan["sell_proceeds"] + 0.01


def test_every_dollar_is_accounted_for(tools) -> None:
    """Proceeds either fund a buy, close the reserve gap, or are named as idle."""
    plan = tools["plan"].invoke({})
    bought = sum(a["dollars"] for a in plan["actions"] if a["action"] == "BUY")
    residual = plan["residual"]
    assert bought + residual["proceeds_not_reinvested"] == pytest.approx(
        plan["sell_proceeds"], abs=0.02
    )
    assert residual["applied_to_cash_reserve_shortfall"] + residual[
        "left_uninvested"
    ] == pytest.approx(residual["proceeds_not_reinvested"], abs=0.02)


def test_a_trim_never_sells_more_than_the_position_holds(tools) -> None:
    ws = RunWorkspace(run_id="verified", base=ARTIFACTS_DIR / "runs")
    positions = {
        r["symbol"]: r["market_value"]
        for r in json.loads((ws.root / PORTFOLIO_DIR / "positions.json").read_text())
    }
    for action in tools["plan"].invoke({})["actions"]:
        for leg in action.get("legs", []):
            assert leg["dollars"] <= positions[leg["symbol"]] + 0.01


def test_a_single_name_breach_in_an_under_target_sector_reports_the_conflict(tools) -> None:
    """Two policy rules genuinely disagree here. The plan must surface that
    rather than silently picking a winner — which rule wins is the client's
    call, not the agent's."""
    conflicts = [
        a for a in tools["plan"].invoke({})["actions"] if a.get("policy_conflict")
    ]
    assert conflicts, "expected VOO to trip the single-name cap inside an under-target sector"
    assert "single-name risk breach" in conflicts[0]["policy_conflict"]


def test_the_plan_admits_what_it_cannot_see(tools) -> None:
    """Recommending a sale without flagging the missing tax data would be the
    most expensive omission in the system."""
    caveats = " ".join(tools["plan"].invoke({})["caveats"]).lower()
    assert "tax lot" in caveats


def test_new_cash_increases_available_funding(tools) -> None:
    base = tools["plan"].invoke({})["total_funding_available"]
    with_cash = tools["plan"].invoke({"new_cash": 25_000.0})["total_funding_available"]
    assert with_cash == pytest.approx(base + 25_000.0)
