"""Tests for the human approval gate.

The interactive half needs a terminal, so what is tested here is the part that
fails silently: parsing the interrupt payload. `pending_from` returning `[]` for
a real interrupt would mean the run continues past a gate that was supposed to
stop it — a safety control that fails open.
"""

from __future__ import annotations

from wealth_agent.cli.approval import pending_from


class FakeInterrupt:
    """Shaped like a LangGraph `Interrupt`."""

    def __init__(self, value):
        self.value = value


def test_no_interrupts_means_nothing_pending() -> None:
    assert pending_from(()) == []
    assert pending_from([]) == []


def test_action_requests_are_extracted() -> None:
    payload = (
        FakeInterrupt(
            {
                "action_requests": [
                    {"action": "place_order", "args": {"symbol": "AAPL", "side": "sell"}}
                ]
            }
        ),
    )
    pending = pending_from(payload)
    assert len(pending) == 1
    assert pending[0]["action"] == "place_order"
    assert pending[0]["args"]["symbol"] == "AAPL"


def test_several_actions_in_one_interrupt_are_all_returned() -> None:
    """Missing one would approve a trade nobody was shown."""
    payload = (
        FakeInterrupt(
            {
                "action_requests": [
                    {"action": "place_order", "args": {"symbol": "AAPL"}},
                    {"action": "place_order", "args": {"symbol": "VOO"}},
                ]
            }
        ),
    )
    assert len(pending_from(payload)) == 2


def test_a_bare_dict_interrupt_still_parses() -> None:
    """Defensive: the payload shape is the harness's to change, not ours."""
    assert pending_from(FakeInterrupt({"action": "place_order"})) == [
        {"action": "place_order"}
    ]


def test_a_list_valued_interrupt_still_parses() -> None:
    assert pending_from(FakeInterrupt([{"action": "place_order"}])) == [
        {"action": "place_order"}
    ]
