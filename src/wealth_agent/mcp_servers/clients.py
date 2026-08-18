"""Connect the agent to the Robinhood MCP servers.

Two things happen here that are worth teaching:

1. **Tools are partitioned by blast radius, not by server.** An MCP server
   hands you one flat list of tools. Some read a balance; some spend money.
   Treating them identically is how you end up explaining an unauthorized trade
   to a compliance officer. :func:`partition_tools` splits them, and the
   supervisor decides separately whether a write tool is even *visible* to the
   model (``ALLOW_WRITE_TOOLS``) and whether it can execute without a human
   (``interrupt_on``).

2. **The transport is swappable.** In ``DEMO_MODE`` the same tool names are
   served from scrubbed fixtures by a local replay server, so the agent code,
   the tool schemas, and the traces are identical whether you are on stage or
   on your own account.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from wealth_agent.config import BANKING_MCP_URL, SETTINGS, TRADING_MCP_URL, Settings
from wealth_agent.mcp_servers.auth import build_oauth_provider

TRADING = "robinhood_trading"
BANKING = "robinhood_banking"

#: Verbs that move money or change account state.
#:
#: Deliberately a denylist of *verbs* rather than an allowlist of tool names:
#: when Robinhood ships `place_options_order` next quarter, an allowlist
#: silently admits it as "read". A verb denylist fails closed on the thing that
#: matters.
#:
#: Note these are verbs only, never nouns. An early version listed ``order``,
#: which classified ``get_orders`` as a write and quietly stripped a tool the
#: portfolio analyst needs. Nouns describe *what* a tool touches; only the verb
#: tells you whether it writes.
MUTATION_VERBS = frozenset(
    {
        "place", "submit", "execute", "cancel", "buy", "sell",
        "create", "update", "modify", "patch", "replace", "delete",
        "remove", "add", "set", "transfer", "deposit", "withdraw",
        "purchase", "pay", "enable", "disable", "revoke", "approve",
    }
)

#: Verbs that only ever read. Must appear as the *first* token to count, so
#: ``get_positions`` is a read but ``sync_and_get_positions`` is not.
READ_VERBS = frozenset(
    {
        "get", "list", "read", "fetch", "search", "find", "view",
        "quote", "describe", "show", "query", "history", "summarize",
    }
)

#: A tool matching neither list is treated as a write. The expensive mistake
#: here is one-directional: mislabeling a read costs a tool, mislabeling a
#: write costs money.
UNKNOWN_IS_WRITE = True

#: Tokens marking a tool that hands back a secret. These are *reads* by every
#: structural test — no verb mutates anything — and they are the last tools you
#: want a model to call.
#:
#: ``banking_get_agent_card_creds`` returns the PAN, expiry and CVV of a real
#: credit card. Nothing about its name says "write", so read/write partitioning
#: alone would hand it to the model, and from there it lands in a context
#: window, a checkpoint, and a LangSmith trace — three copies of a card number,
#: in systems never designed to hold one.
#:
#: Blast radius is not two-valued. "Can this change my account?" and "can this
#: leak something that cannot be un-leaked?" are different questions, and a
#: classifier that only asks the first one answers confidently and wrongly.
SECRET_TOKENS = frozenset(
    {"creds", "credentials", "credential", "cvv", "pan", "secret", "password", "pin"}
)

_TOKEN_RE = re.compile(r"[^a-z0-9]+")


def _tokens(name: str) -> list[str]:
    """Split a tool name into lowercase word tokens.

    Handles ``snake_case``, ``camelCase`` and ``kebab-case`` alike, so the
    classifier does not depend on one server's naming convention.
    """
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return [t for t in _TOKEN_RE.split(spaced.lower()) if t]


@dataclass
class ToolSplit:
    """Tools grouped by whether calling one can change account state."""

    read: list[BaseTool] = field(default_factory=list)
    write: list[BaseTool] = field(default_factory=list)
    #: Tools that hand back a secret. Never included in :attr:`all` — there is
    #: no flag that turns these on, because there is no version of this
    #: application that should read a card number.
    secret: list[BaseTool] = field(default_factory=list)

    @property
    def all(self) -> list[BaseTool]:
        return [*self.read, *self.write]

    def names(self) -> dict[str, list[str]]:
        return {
            "read": sorted(t.name for t in self.read),
            "write": sorted(t.name for t in self.write),
            "secret": sorted(t.name for t in self.secret),
        }


@dataclass
class ServerTools:
    """One server's tools, plus an honest record of where they came from.

    ``live`` is not decoration. A memo built partly on fixtures and partly on a
    real account is a different document from one built entirely on either, and
    the run has to be able to say which it was. Provenance travels with the
    tools so nothing downstream has to guess.
    """

    server: str
    split: ToolSplit
    live: bool
    fallback_reason: str | None = None


def classify_tool(name: str, *, namespace: str | None = None) -> str:
    """Return ``"secret"``, ``"write"`` or ``"read"`` for a tool name.

    Order matters. A secret-bearing token wins outright, then a mutation verb
    anywhere (so ``search_and_buy`` is a write), then a leading read verb.
    Anything else is a write when :data:`UNKNOWN_IS_WRITE`.

    Args:
        name: The tool name as the server reports it.
        namespace: The server name, when known. Servers that prefix their tools
            with their own name push the real verb off the front —
            ``banking_get_agent_card_transactions`` is plainly a read, but the
            leading-verb rule sees ``banking`` and fails closed on all seven of
            that server's tools at once. Stripping a prefix that matches the
            server's own name recovers the verb without loosening the rule for
            anything else: ``sync_and_get_positions`` stays a write, because
            ``sync`` is not a namespace.

    Examples:
        >>> classify_tool("get_orders")
        'read'
        >>> classify_tool("place_order")
        'write'
        >>> classify_tool("banking_get_agent_card_transactions",
        ...               namespace="robinhood_banking")
        'read'
        >>> classify_tool("banking_get_agent_card_creds",
        ...               namespace="robinhood_banking")
        'secret'
        >>> classify_tool("sync_and_get_positions", namespace="robinhood_banking")
        'write'
        >>> classify_tool("frobnicate_widget")
        'write'
    """
    tokens = _tokens(name)
    if not tokens:
        return "write"
    if SECRET_TOKENS & set(tokens):
        return "secret"
    if MUTATION_VERBS & set(tokens):
        return "write"
    if namespace:
        prefixes = set(_tokens(namespace))
        while len(tokens) > 1 and tokens[0] in prefixes:
            tokens = tokens[1:]
    if tokens[0] in READ_VERBS:
        return "read"
    return "write" if UNKNOWN_IS_WRITE else "read"


def partition_tools(tools: list[BaseTool], *, namespace: str | None = None) -> ToolSplit:
    """Split MCP tools by blast radius."""
    split = ToolSplit()
    for tool in tools:
        bucket = classify_tool(tool.name, namespace=namespace)
        getattr(split, bucket).append(tool)
    return split


def _live_connections() -> dict[str, dict]:
    """Streamable-HTTP connections with OAuth attached to each server.

    Each server gets its own provider, and therefore its own token cache: the
    trading and banking servers are separate OAuth resources and a token minted
    for one is not valid for the other.
    """
    return {
        TRADING: {
            "transport": "streamable_http",
            "url": TRADING_MCP_URL,
            "auth": build_oauth_provider(TRADING, TRADING_MCP_URL),
        },
        BANKING: {
            "transport": "streamable_http",
            "url": BANKING_MCP_URL,
            "auth": build_oauth_provider(BANKING, BANKING_MCP_URL),
        },
    }


def _replay_connections() -> dict[str, dict]:
    """Stdio connections to the local fixture replay server.

    Same tool names, same schemas, scrubbed data. Because it is a real MCP
    server rather than a mock, the tool-loading path, the LangSmith spans, and
    the agent's view of the world are byte-for-byte what they'd be live.
    """
    return {
        server: {
            "transport": "stdio",
            "command": "python",
            "args": ["-m", "wealth_agent.mcp_servers.replay_server", "--server", server],
        }
        for server in (TRADING, BANKING)
    }


def build_client(settings: Settings | None = None) -> MultiServerMCPClient:
    """Build an MCP client for the current mode (live or replay)."""
    settings = settings or SETTINGS
    connections = _live_connections() if settings.is_live else _replay_connections()
    return MultiServerMCPClient(connections)


async def load_tools(
    server: str,
    *,
    settings: Settings | None = None,
    client: MultiServerMCPClient | None = None,
) -> ToolSplit:
    """Load and partition the tools exposed by one Robinhood MCP server.

    Args:
        server: :data:`TRADING` or :data:`BANKING`.
        settings: Overrides the process-wide settings; mainly for tests.
        client: Reuse an existing client instead of building one.

    Returns:
        The server's tools, split by blast radius. Write tools are dropped
        entirely unless ``ALLOW_WRITE_TOOLS`` is on — a model cannot misuse a
        tool it was never shown.
    """
    settings = settings or SETTINGS
    client = client or build_client(settings)
    split = partition_tools(await client.get_tools(server_name=server), namespace=server)
    if not settings.allow_write_tools:
        split.write = []
    return split


#: What the analytics layer needs, and every tool name known to provide it.
#:
#: The replay fixtures and the real servers do not agree on names — live
#: trading calls it ``get_equity_positions``, the fixtures call it
#: ``get_positions``, and banking prefixes everything. Binding the analytics
#: tools to a *capability* rather than a string keeps one code path for both,
#: and makes adding a third backend a line in this table.
#:
#: Order is preference order: the first name present wins.
CAPABILITIES: dict[str, tuple[str, ...]] = {
    "positions": ("get_positions", "get_equity_positions"),
    "balances": ("get_account_balances", "get_portfolio", "get_accounts"),
    "card_transactions": ("get_card_transactions", "banking_get_agent_card_transactions"),
}


class CapabilityError(RuntimeError):
    """No connected server offers a capability the agent needs."""


def resolve_capability(capability: str, by_name: dict[str, BaseTool]) -> BaseTool:
    """Find the tool providing ``capability`` among the loaded tools.

    Raises:
        CapabilityError: With the candidates it looked for and what was
            actually available. A bare ``KeyError: 'get_positions'`` from three
            frames down tells you a dictionary lookup failed; it does not tell
            you that you are connected to a server that spells it differently.
    """
    for name in CAPABILITIES[capability]:
        if name in by_name:
            return by_name[name]
    msg = (
        f"No tool provides `{capability}`. Looked for "
        f"{', '.join(CAPABILITIES[capability])}; the connected servers offer "
        f"{', '.join(sorted(by_name)) or '(nothing)'}. Add the server's name "
        f"to CAPABILITIES in mcp_clients.py if it is spelled differently."
    )
    raise CapabilityError(msg)


def root_cause(exc: BaseException, *, depth: int = 0) -> str:
    """Flatten an ``ExceptionGroup`` down to the message that explains itself.

    MCP connection failures arrive wrapped in an anyio ``TaskGroup``, so the
    outermost exception is always ``unhandled errors in a TaskGroup`` — which
    tells you nothing. The useful sentence (``client id not allowed: ...``,
    ``Protected resource ... does not match expected ...``) is two or three
    levels down.
    """
    if depth < 5 and isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        return root_cause(exc.exceptions[0], depth=depth + 1)
    return f"{type(exc).__name__}: {exc}"


#: One read tool per server that the run is going to call anyway, used to check
#: the server will actually answer before we commit to it.
_PROBE_TOOLS: dict[str, tuple[str, ...]] = {
    TRADING: CAPABILITIES["positions"],
    BANKING: CAPABILITIES["card_transactions"],
}


async def _probe_liveness(server: str, split: ToolSplit) -> None:
    """Call one read tool for real, because listing tools is not the same as
    being able to use them.

    Robinhood's banking server is the case that forced this. It completes the
    OAuth flow, advertises seven tools, and *then* answers `401 client id not
    allowed` on every call, because it admits only pre-registered MCP clients.
    Listing succeeded, so the old fallback saw a healthy live server and handed
    those tools to the agent — and the failure surfaced mid-run, once per call,
    each time re-triggering the browser authorization flow. A single run opened
    five OAuth tabs and then died.

    Failing here instead costs one call and degrades the server to fixtures
    once, loudly, before any model tokens are spent.
    """
    for name in _PROBE_TOOLS.get(server, ()):
        tool = next((t for t in split.read if t.name == name), None)
        if tool is not None:
            await tool.ainvoke({})
            return


async def load_server_tools(
    server: str,
    *,
    settings: Settings | None = None,
    client: MultiServerMCPClient | None = None,
    allow_fallback: bool = True,
) -> ServerTools:
    """Load one server's tools, degrading to fixtures if the live server refuses.

    In demo mode this is just :func:`load_tools`. In live mode it tries the real
    server first and, if that fails, falls back to *that one server's* replay
    fixtures rather than taking the whole run down with it.

    The partial-failure case is the normal case here, not a hypothetical.
    Robinhood's two MCP servers have different admission policies: trading
    accepts a dynamically-registered client, and banking answers ``401 client
    id not allowed`` to one — it admits only pre-registered clients like Claude
    or ChatGPT. Both facts are true simultaneously and permanently, so an
    all-or-nothing connection strategy means the live path is simply unusable
    despite half of it working perfectly.

    The fallback is loud rather than silent: the reason is preserved on the
    returned :class:`ServerTools` and printed by the CLI, because "some of this
    memo is about your real money and some of it is invented" is exactly the
    kind of thing that must never be inferred from a quiet log line.

    Args:
        server: :data:`TRADING` or :data:`BANKING`.
        settings: Overrides the process-wide settings.
        client: Reuse an existing client for the primary attempt.
        allow_fallback: Set ``False`` to make a live failure raise, which is
            what you want in a test or an unattended job.

    Returns:
        The server's tools and where they came from.
    """
    settings = settings or SETTINGS
    if not settings.is_live:
        return ServerTools(
            server=server,
            split=await load_tools(server, settings=settings, client=client),
            live=False,
        )

    try:
        split = await load_tools(server, settings=settings, client=client)
        await _probe_liveness(server, split)
    except Exception as exc:  # noqa: BLE001 — any failure to reach a live server
        if not allow_fallback:
            raise
        reason = root_cause(exc)
        replay = MultiServerMCPClient({server: _replay_connections()[server]})
        return ServerTools(
            server=server,
            split=await load_tools(server, settings=Settings(demo_mode=True,
                                   allow_write_tools=settings.allow_write_tools),
                                   client=replay),
            live=False,
            fallback_reason=reason,
        )
    return ServerTools(server=server, split=split, live=True)


__all__ = [
    "BANKING",
    "CAPABILITIES",
    "TRADING",
    "CapabilityError",
    "ServerTools",
    "ToolSplit",
    "build_client",
    "classify_tool",
    "load_server_tools",
    "load_tools",
    "partition_tools",
    "resolve_capability",
    "root_cause",
]
