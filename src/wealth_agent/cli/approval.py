"""The human gate, rendered.

`interrupt_on` has been configured on every write tool since the first version
of this repo, and it was never exercised — which is the most common state for a
safety control. A gate nobody has watched fire is a gate nobody knows the shape
of.

What the person approving actually needs is not "the agent wants to call
place_order". It is the trade, in money, next to the policy rule that produced
it and the evidence behind it. That is the difference between a confirmation
dialog and an approval.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

console = Console()


def _render(requests: list[dict[str, Any]]) -> None:
    table = Table(box=None, padding=(0, 2))
    table.add_column("#", justify="right", style="dim")
    table.add_column("action")
    table.add_column("details", overflow="fold")
    for i, request in enumerate(requests, start=1):
        action = request.get("action") or request.get("name") or "?"
        args = request.get("args") or {}
        detail = "  ".join(f"[dim]{k}[/dim] {v}" for k, v in args.items())
        table.add_row(str(i), f"[bold]{action}[/bold]", detail or "[dim](no arguments)[/dim]")
    console.print(
        Panel(
            table,
            title="[bold yellow]approval required[/bold yellow]",
            subtitle="[dim]nothing has been sent to the broker[/dim]",
            border_style="yellow",
            padding=(1, 2),
        )
    )


def ask(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Show the pending actions and collect one decision per action.

    Returns the `decisions` payload for `Command(resume=...)`.

    Rejection carries a message back to the model rather than silently dropping
    the call, so the agent can say in the memo that a recommended trade was
    declined — an approval gate whose refusals vanish teaches the agent nothing
    and leaves the memo claiming something that did not happen.
    """
    _render(requests)
    decisions: list[dict[str, Any]] = []
    for i, request in enumerate(requests, start=1):
        name = request.get("action") or request.get("name") or f"action {i}"
        choice = Prompt.ask(
            f"  [bold]{name}[/bold] — [green]a[/green]pprove, "
            f"[red]r[/red]eject, or e[yellow]x[/yellow]plain why not",
            choices=["a", "r", "x"],
            default="r",
        )
        if choice == "a":
            decisions.append({"type": "approve"})
            console.print("    [green]approved[/green]")
        elif choice == "x":
            why = Prompt.ask("    reason the model should hear")
            decisions.append({"type": "reject", "message": why})
            console.print("    [yellow]rejected, with a reason[/yellow]")
        else:
            decisions.append(
                {"type": "reject", "message": "The human declined this trade."}
            )
            console.print("    [red]rejected[/red]")
    return decisions


def pending_from(payload: Any) -> list[dict[str, Any]]:
    """Pull the action requests out of whatever shape the interrupt arrived in."""
    interrupts = payload if isinstance(payload, (list, tuple)) else [payload]
    requests: list[dict[str, Any]] = []
    for item in interrupts:
        value = getattr(item, "value", item)
        if isinstance(value, dict) and "action_requests" in value:
            requests.extend(value["action_requests"])
        elif isinstance(value, list):
            requests.extend(v for v in value if isinstance(v, dict))
        elif isinstance(value, dict):
            requests.append(value)
    return requests


__all__ = ["ask", "pending_from"]
