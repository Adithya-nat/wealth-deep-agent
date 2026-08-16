"""Tool classification decides what the model can touch. It fails closed."""

from __future__ import annotations

import pytest

from wealth_agent.config import Settings
from wealth_agent.mcp_clients import (
    BANKING,
    TRADING,
    build_client,
    classify_tool,
    load_tools,
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        # Nouns must not trigger. An early version listed "order" as a write
        # verb and silently stripped get_orders from the portfolio analyst.
        ("get_orders", "read"),
        ("get_open_orders", "read"),
        ("get_positions", "read"),
        ("get_card_transactions", "read"),
        ("listWatchlists", "read"),
        ("list-watchlists", "read"),
        # Verbs anywhere in the name win.
        ("place_order", "write"),
        ("cancel_order", "write"),
        ("update_card_settings", "write"),
        ("search_and_buy", "write"),
        ("sell_all", "write"),
        ("transfer_funds", "write"),
        # Unrecognised is a write: mislabeling a read costs a tool,
        # mislabeling a write costs money.
        ("frobnicate_widget", "write"),
        ("", "write"),
    ],
)
def test_classification(name: str, expected: str) -> None:
    assert classify_tool(name) == expected


async def test_replay_servers_expose_the_expected_surface() -> None:
    settings = Settings(demo_mode=True, allow_write_tools=True)
    client = build_client(settings)

    trading = await load_tools(TRADING, settings=settings, client=client)
    assert "get_positions" in trading.names()["read"]
    assert "get_orders" in trading.names()["read"]
    assert trading.names()["write"] == ["place_order"]

    banking = await load_tools(BANKING, settings=settings, client=client)
    assert "get_card_transactions" in banking.names()["read"]
    assert banking.names()["write"] == ["update_card_settings"]


async def test_write_tools_are_hidden_unless_allowed() -> None:
    """A model cannot misuse a tool it was never shown."""
    settings = Settings(demo_mode=True, allow_write_tools=False)
    client = build_client(settings)
    trading = await load_tools(TRADING, settings=settings, client=client)
    assert trading.write == []
    assert trading.read, "read tools must survive"
