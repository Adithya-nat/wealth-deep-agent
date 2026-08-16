"""Middleware that records every tool result to the grounding ledger.

This is the part people skip, and it is the part that makes everything
downstream possible.

The obvious implementation is to have each tool write its own ledger entry. It
works right up until someone adds the eleventh tool and forgets — and the
failure is silent, because a missing ledger entry doesn't raise, it just makes a
true claim look fabricated at verification time. Debugging *that* is miserable.

Middleware inverts it. ``wrap_tool_call`` sits between the agent and every tool
it has, including the ones loaded dynamically from MCP servers and the ones
deep agents installs itself. There is no opt-in, so there is nothing to forget.

The same hook is where you would put redaction, rate limiting, or per-tool
timeouts. Recording is just the cheapest useful thing to do with it.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from wealth_agent.store import GroundingLedger

#: Tools whose results are agent bookkeeping rather than evidence about the
#: world. Recording them would flood the grounded set with todo text and file
#: listings, which weakens the numeric check: the more text in the ledger, the
#: more likely a fabricated number coincidentally appears somewhere in it.
#:
#: `read_file` is on this list for a sharper reason, and it is worth stating
#: because the bug it caused was invisible. The agent reads its own skills at
#: startup, and `skills/memo-format/SKILL.md` contains worked examples —
#: including `"roughly $140,000"` presented as a *negative* example of rounding
#: past tolerance. Recording that read put $140,000 into the grounded set, which
#: would have made the exact defect the skill warns against verify successfully.
#:
#: The general rule: **instructions are not evidence.** Anything the agent reads
#: about how to do its job must never become something it can cite. Nothing is
#: lost by excluding `read_file` — fetched sources enter the ledger at fetch
#: time via `write_source`, and tool results enter when the tool returns, so a
#: re-read is always of something already recorded.
NON_EVIDENCE_TOOLS = frozenset(
    {
        "write_todos",
        "ls",
        "glob",
        "grep",
        "read_file",
        "edit_file",
        "write_file",
        "delete",
        "task",
    }
)


def _content_of(result: ToolMessage | Command[Any]) -> str:
    """Best-effort text of a tool result, whatever shape it came back in."""
    message: Any = result
    if isinstance(result, Command):
        update = result.update if isinstance(result.update, dict) else {}
        messages = update.get("messages") or []
        message = messages[-1] if messages else None
    content = getattr(message, "content", None)
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    # Content blocks: concatenate the text parts, drop images and the like.
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and "text" in block:
            parts.append(str(block["text"]))
    return "\n".join(parts)


class GroundingLedgerMiddleware(AgentMiddleware):
    """Append every evidence-bearing tool result to the run's ledger.

    Args:
        ledger: The run's ledger.
        agent_name: Which agent this middleware instance is installed on.
            Recorded per entry so you can answer "which subagent saw this
            number?" — the question that comes up the moment a claim looks
            wrong.
    """

    def __init__(self, ledger: GroundingLedger, agent_name: str = "supervisor") -> None:
        super().__init__()
        self.ledger = ledger
        self.agent_name = agent_name

    @property
    def name(self) -> str:
        return f"GroundingLedgerMiddleware[{self.agent_name}]"

    def _record(self, request: ToolCallRequest, result: ToolMessage | Command[Any]) -> None:
        tool_name = request.tool_call.get("name", "unknown")
        if tool_name in NON_EVIDENCE_TOOLS:
            return
        content = _content_of(result)
        if not content:
            return
        self.ledger.record(
            kind="tool_result",
            name=tool_name,
            agent=self.agent_name,
            args=_json_safe(request.tool_call.get("args", {})),
            content=content,
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        result = handler(request)
        self._record(request, result)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        result = await handler(request)
        self._record(request, result)
        return result


def _json_safe(value: Any) -> Any:
    """Coerce tool args into something json.dumps will accept."""
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return {"repr": repr(value)}
    return value


__all__ = ["GroundingLedgerMiddleware", "NON_EVIDENCE_TOOLS"]
