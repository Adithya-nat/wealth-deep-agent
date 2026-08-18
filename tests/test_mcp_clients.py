"""Tool classification decides what the model can touch. It fails closed."""

from __future__ import annotations

import pytest

from wealth_agent.config import Settings
from wealth_agent.mcp_servers.clients import (
    BANKING,
    TRADING,
    CapabilityError,
    build_client,
    classify_tool,
    load_server_tools,
    load_tools,
    partition_tools,
    resolve_capability,
    root_cause,
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


# --------------------------------------------------------------------------
# Partial live failure
#
# Robinhood's two servers have different admission policies: trading accepts a
# dynamically-registered OAuth client, banking answers `401 client id not
# allowed` to one. Both are true at once and permanently, so an all-or-nothing
# connection strategy makes the live path unusable despite half of it working.
# --------------------------------------------------------------------------


class _RefusingClient:
    """Stands in for a server that authenticates and then refuses the client."""

    def __init__(self, message: str) -> None:
        self._message = message

    async def get_tools(self, server_name: str) -> list:  # noqa: ARG002
        raise ExceptionGroup(
            "unhandled errors in a TaskGroup",
            [RuntimeError(self._message)],
        )


async def test_live_failure_falls_back_to_that_server_only() -> None:
    settings = Settings(demo_mode=False, allow_write_tools=False)
    source = await load_server_tools(
        BANKING,
        settings=settings,
        client=_RefusingClient("client id not allowed: LtLiNmbs9ow"),
    )

    # The run continues on fixtures rather than dying...
    assert source.split.read, "fixture tools must be loaded"
    assert "get_card_transactions" in source.split.names()["read"]
    # ...but never claims to be live, and preserves why.
    assert source.live is False
    assert "client id not allowed" in (source.fallback_reason or "")


async def test_fallback_reason_unwraps_the_task_group() -> None:
    """The outermost exception is always uninformative; the cause is nested."""
    nested = ExceptionGroup("outer", [ExceptionGroup("inner", [ValueError("the real reason")])])
    assert root_cause(nested) == "ValueError: the real reason"


async def test_fallback_can_be_refused() -> None:
    """Unattended callers want the crash, not a quiet substitution."""
    settings = Settings(demo_mode=False, allow_write_tools=False)
    with pytest.raises(ExceptionGroup):
        await load_server_tools(
            BANKING,
            settings=settings,
            client=_RefusingClient("client id not allowed"),
            allow_fallback=False,
        )


async def test_demo_mode_is_never_marked_live() -> None:
    settings = Settings(demo_mode=True, allow_write_tools=False)
    source = await load_server_tools(TRADING, settings=settings)
    assert source.live is False
    assert source.fallback_reason is None, "fixtures by choice is not a degradation"


# --------------------------------------------------------------------------
# Namespaced tool names and secret-bearing reads
#
# Both discovered by probing the real servers. The live surface disagrees with
# the fixtures on every name that matters, and one "read" hands back a CVV.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        # A server prefix must not push the verb out of view. All seven live
        # banking tools failed closed on this before the namespace was passed.
        ("banking_get_agent_card_transactions", "read"),
        ("banking_get_agent_card_balance", "read"),
        ("banking_get_agent_card_status", "read"),
        # Stripping is limited to the server's own name, so this stays a write.
        ("sync_and_get_positions", "write"),
        # Mutation verbs still win over any prefix.
        ("banking_update_card_settings", "write"),
    ],
)
def test_namespaced_classification(name: str, expected: str) -> None:
    assert classify_tool(name, namespace=BANKING) == expected


def test_the_prefix_only_helps_its_own_server() -> None:
    """`banking_*` means nothing to the trading server."""
    assert classify_tool("banking_get_agent_card_balance", namespace=TRADING) == "write"


@pytest.mark.parametrize(
    "name",
    [
        "banking_get_agent_card_creds",
        "get_card_credentials",
        "read_cvv",
        "fetch_account_password",
    ],
)
def test_secret_bearing_reads_are_their_own_category(name: str) -> None:
    """Structurally a read; catastrophic to expose. Neither bucket fits."""
    assert classify_tool(name, namespace=BANKING) == "secret"


def test_secret_tools_are_never_exposed_even_with_writes_allowed() -> None:
    class _Tool:
        def __init__(self, name: str) -> None:
            self.name = name

    split = partition_tools(
        [_Tool("banking_get_agent_card_transactions"),
         _Tool("banking_get_agent_card_creds"),
         _Tool("place_equity_order")],
        namespace=BANKING,
    )
    assert split.names()["read"] == ["banking_get_agent_card_transactions"]
    assert split.names()["secret"] == ["banking_get_agent_card_creds"]
    # `all` is what the agent is built from, and there is no flag that adds it.
    assert "banking_get_agent_card_creds" not in [t.name for t in split.all]


# --------------------------------------------------------------------------
# Capability resolution
# --------------------------------------------------------------------------


def test_capabilities_resolve_across_both_naming_schemes() -> None:
    class _Tool:
        def __init__(self, name: str) -> None:
            self.name = name

    fixtures = {n: _Tool(n) for n in ("get_positions", "get_account_balances",
                                      "get_card_transactions")}
    live = {n: _Tool(n) for n in ("get_equity_positions", "get_portfolio",
                                  "banking_get_agent_card_transactions")}

    for by_name in (fixtures, live):
        for capability in ("positions", "balances", "card_transactions"):
            assert resolve_capability(capability, by_name) is not None


def test_a_missing_capability_says_what_it_looked_for() -> None:
    with pytest.raises(CapabilityError) as excinfo:
        resolve_capability("positions", {})
    message = str(excinfo.value)
    assert "get_equity_positions" in message, "must list the candidates"
    assert "(nothing)" in message, "must say what was actually available"
