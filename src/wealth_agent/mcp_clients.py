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
from wealth_agent.mcp_auth import build_oauth_provider

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

    @property
    def all(self) -> list[BaseTool]:
        return [*self.read, *self.write]

    def names(self) -> dict[str, list[str]]:
        return {
            "read": sorted(t.name for t in self.read),
            "write": sorted(t.name for t in self.write),
        }


def classify_tool(name: str) -> str:
    """Return ``"write"`` or ``"read"`` for a tool name.

    A mutation verb anywhere wins, so ``search_and_buy`` is a write. Otherwise
    a leading read verb makes it a read. Anything else is a write when
    :data:`UNKNOWN_IS_WRITE`.

    Examples:
        >>> classify_tool("get_orders")
        'read'
        >>> classify_tool("place_order")
        'write'
        >>> classify_tool("frobnicate_widget")
        'write'
    """
    tokens = _tokens(name)
    if not tokens:
        return "write"
    if MUTATION_VERBS & set(tokens):
        return "write"
    if tokens[0] in READ_VERBS:
        return "read"
    return "write" if UNKNOWN_IS_WRITE else "read"


def partition_tools(tools: list[BaseTool]) -> ToolSplit:
    """Split MCP tools into read-only and state-changing groups."""
    split = ToolSplit()
    for tool in tools:
        if classify_tool(tool.name) == "write":
            split.write.append(tool)
        else:
            split.read.append(tool)
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
            "args": ["-m", "wealth_agent.replay_server", "--server", server],
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
    split = partition_tools(await client.get_tools(server_name=server))
    if not settings.allow_write_tools:
        split.write = []
    return split


__all__ = [
    "BANKING",
    "TRADING",
    "ToolSplit",
    "build_client",
    "classify_tool",
    "load_tools",
    "partition_tools",
]
