"""The live panel: what the agent is doing, while it is doing it.

The problem this solves is not cosmetic. A verified run makes dozens of model
calls across five agents and takes minutes, and the previous behaviour was a
silent terminal followed by a wall of markdown. That is unpleasant on your own
laptop and fatal in front of an audience, because a room watching a blank prompt
concludes the thing is broken well before it finishes.

It is also the cheapest observability you will ever add. `astream` with
`stream_mode=["updates", "custom"]` gives you node transitions and whatever your
middleware chooses to emit, which is enough to show which subagent holds the
floor, which tool just returned, how many todos are done, and what the run has
cost so far. None of that required instrumenting the agent — the graph was
already emitting it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

#: How many recent tool calls the activity log keeps on screen.
#:
#: The panel above it answers "where is the run now"; this answers "what has it
#: actually done". Both matter and they are different questions — a run can look
#: alive in the panel while the log shows it calling `read_file` twelve times.
LOG_LINES = 12

#: Display order. The supervisor is first because it is the one delegating; the
#: rest appear in the order the run actually reaches them.
AGENT_ORDER = [
    "supervisor",
    "portfolio-analyst",
    "spend-analyst",
    "allocation-strategist",
    "market-researcher",
    "verifier",
]

#: A stable colour per agent, so the log reads as columns rather than as text.
_STYLE = {
    "supervisor": "cyan",
    "portfolio-analyst": "green",
    "spend-analyst": "yellow",
    "allocation-strategist": "magenta",
    "market-researcher": "blue",
    "verifier": "red",
}

SHORT = {
    "supervisor": "plan",
    "portfolio-analyst": "portfolio",
    "spend-analyst": "spend",
    "allocation-strategist": "strategy",
    "market-researcher": "research",
    "verifier": "verify",
}


@dataclass
class AgentState:
    """What one row of the panel knows."""

    status: str = "idle"  # idle | active | done
    last_tool: str = ""
    tool_calls: int = 0


@dataclass
class LogLine:
    """One thing that happened, with the agent that did it."""

    at: float
    agent: str
    tool: str
    note: str = ""


@dataclass
class RunProgress:
    """Mutable view of a run in flight."""

    started: float = field(default_factory=time.monotonic)
    agents: dict[str, AgentState] = field(default_factory=dict)
    log: list[LogLine] = field(default_factory=list)
    todos_total: int = 0
    todos_done: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    cache_hit_rate: float = 0.0
    message: str = ""

    @property
    def elapsed(self) -> str:
        seconds = int(time.monotonic() - self.started)
        return f"{seconds // 60:d}:{seconds % 60:02d}"

    def state(self, agent: str) -> AgentState:
        return self.agents.setdefault(agent, AgentState())

    def note(self, agent: str, tool: str, note: str = "") -> None:
        self.log.append(LogLine(time.monotonic() - self.started, agent, tool, note))

    def _log_table(self) -> Table:
        table = Table.grid(padding=(0, 1))
        table.add_column(width=6, justify="right")
        table.add_column(width=12)
        table.add_column(overflow="ellipsis")
        for line in self.log[-LOG_LINES:]:
            table.add_row(
                Text(f"{int(line.at) // 60}:{int(line.at) % 60:02d}", style="dim"),
                Text(SHORT.get(line.agent, line.agent)[:12], style=_STYLE.get(line.agent, "white")),
                Text.from_markup(
                    f"[bold]{line.tool}[/bold]" + (f"  [dim]{line.note}[/dim]" if line.note else "")
                ),
            )
        return table

    def render(self, *, title: str) -> Panel:
        table = Table.grid(padding=(0, 1))
        table.add_column(width=2)
        table.add_column(width=10)
        table.add_column(overflow="ellipsis")

        seen = [a for a in AGENT_ORDER if a in self.agents]
        seen += [a for a in self.agents if a not in AGENT_ORDER]
        for agent in seen:
            state = self.agents[agent]
            mark, style = {
                "done": ("✓", "green"),
                "active": ("●", "cyan"),
            }.get(state.status, ("○", "dim"))
            detail = state.last_tool or ("—" if state.status == "idle" else "working")
            if state.tool_calls:
                detail = f"{detail}  [dim]({state.tool_calls} call{'' if state.tool_calls == 1 else 's'})[/dim]"
            table.add_row(
                Text(mark, style=style),
                Text(SHORT.get(agent, agent)[:10], style=style),
                Text.from_markup(detail, style="dim" if state.status == "idle" else ""),
            )

        if not seen:
            table.add_row(Text("○", style="dim"), Text("starting", style="dim"), Text(""))

        todos = (
            f"{self.todos_done}/{self.todos_total} todos" if self.todos_total else "planning"
        )
        cache = f" · {self.cache_hit_rate:.0%} cached" if self.cache_hit_rate else ""
        footer = Text.from_markup(
            f"[bold]{self.elapsed}[/bold]  ·  {todos}  ·  "
            f"{self.tokens / 1000:,.0f}k tokens  ·  [bold]${self.cost_usd:,.2f}[/bold]{cache}"
        )
        body: list[Any] = [table]
        if self.log:
            body += [
                Text(""),
                Text("─" * 3 + " activity " + "─" * 3, style="dim"),
                self._log_table(),
            ]
        body += [Text(""), footer]
        if self.message:
            body.append(Text.from_markup(f"[yellow]{self.message}[/yellow]"))
        return Panel(Group(*body), title=title, border_style="cyan", padding=(1, 2))


#: A short, human phrase for the tools whose names do not explain themselves.
_TOOL_NOTES = {
    "task": "delegating to a subagent",
    "write_todos": "planning",
    "load_portfolio": "pulling positions and balances",
    "load_spend_data": "pulling the card feed",
    "rebalance_plan": "computing the trades",
    "drift_report": "measuring against policy",
    "cash_runway": "sizing the reserve",
    "web_search": "searching",
    "fetch_page": "reading a page",
    "verify_report": "checking the memo",
    "write_file": "writing the memo",
    "edit_file": "revising the memo",
}


def _describe(tool: str, payload: dict[str, Any]) -> str:
    note = payload.get("note") or _TOOL_NOTES.get(tool, "")
    return str(note)


def consume(progress: RunProgress, mode: str, payload: Any) -> None:
    """Fold one streamed event into the progress view.

    Agent rows come from `custom` events emitted by the middleware, not from
    `updates` node names. The first version parsed node names and attributed
    every subagent's work to the supervisor, because deep agents run subagents
    as nested graphs whose node names carry no agent identity. The middleware
    already knows which agent it is attached to, so it says so — and those
    events propagate up from nested graphs on their own.
    """
    if mode == "custom" and isinstance(payload, dict):
        event = payload.get("event")
        if event == "cost":
            progress.tokens = int(payload.get("tokens") or 0)
            progress.cost_usd = float(payload.get("cost_usd") or 0.0)
            progress.cache_hit_rate = float(payload.get("cache_hit_rate") or 0.0)
        elif event == "tool":
            agent = str(payload.get("agent") or "supervisor")
            tool = str(payload.get("tool") or "")
            state = progress.state(agent)
            state.status = "active"
            state.tool_calls += 1
            state.last_tool = f"{tool}()"
            progress.note(agent, tool, _describe(tool, payload))
            # Delegation: the supervisor hands the floor to a subagent and gets
            # it back when the `task` result returns.
            if tool == "task":
                for name, other in progress.agents.items():
                    if name != agent and other.status == "active":
                        other.status = "done"
        elif event == "agent_done":
            progress.state(str(payload.get("agent"))).status = "done"
        elif event == "verification":
            passed = bool(payload.get("passed"))
            failures = int(payload.get("failures") or 0)
            progress.note(
                "supervisor",
                "verification",
                f"{float(payload.get('score') or 0):.1%} grounded — "
                + ("passed" if passed else f"{failures} to fix, revising"),
            )
            progress.message = (
                "" if passed else f"verification found {failures} claim(s) to fix"
            )
        return

    if mode != "updates" or not isinstance(payload, dict):
        return

    for update in payload.values():
        if not isinstance(update, dict):
            continue
        todos = update.get("todos")
        if isinstance(todos, list) and todos:
            progress.todos_total = len(todos)
            progress.todos_done = sum(
                1
                for t in todos
                if isinstance(t, dict) and t.get("status") == "completed"
            )


def unpack(event: Any) -> tuple[str, Any]:
    """Normalize a stream event to `(mode, payload)`.

    With `subgraphs=True` — which is required for a nested subagent's custom
    events to reach the parent stream at all — LangGraph yields
    `(namespace, mode, payload)` three-tuples instead of `(mode, payload)`.
    Handling both keeps this working whichever way the caller streams.

    Getting this wrong is quiet: without `subgraphs=True` the run streams
    perfectly, the panel updates, and every subagent's activity is simply
    absent. The supervisor appears to be doing all the work alone.
    """
    if isinstance(event, tuple) and len(event) == 3:
        _namespace, mode, payload = event
        return str(mode), payload
    mode, payload = event
    return str(mode), payload


async def track(
    stream: AsyncIterator[Any],
    *,
    title: str,
    console: Console | None = None,
    progress: RunProgress | None = None,
) -> RunProgress:
    """Drive the panel from a `astream(stream_mode=[...])` iterator.

    Returns the final progress so a caller can report elapsed time and cost
    without re-deriving them.
    """
    console = console or Console()
    view = progress or RunProgress()
    with Live(view.render(title=title), console=console, refresh_per_second=6) as live:
        async for event in stream:
            mode, payload = unpack(event)
            consume(view, mode, payload)
            live.update(view.render(title=title))
        for state in view.agents.values():
            state.status = "done"
        live.update(view.render(title=title))
    return view


__all__ = ["AGENT_ORDER", "LOG_LINES", "AgentState", "LogLine", "RunProgress", "consume", "track", "unpack"]
