"""Numbered prompts, so nobody has to remember a flag.

Two deliberate choices.

**Numbers, not arrow keys.** A highlight moving up and down a list is invisible
to a room watching a shared screen; "3" being typed is not. The same reasoning
applies to a recording, and to anyone reading over your shoulder while you teach
them the tool.

**Every option carries its price and its duration.** "Full review ~5 min ~$1.10"
is the difference between a menu and an interface: the choice you are actually
making is a cost/quality trade, and a menu that hides that is asking you to pick
blind.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table

from wealth_agent.config import ARTIFACTS_DIR
from wealth_agent.data.store import RUNS_DIR, RunWorkspace

console = Console()


@dataclass
class Choice:
    """One numbered option."""

    key: str
    label: str
    detail: str = ""
    cost: str = ""


def choose(title: str, options: list[Choice], *, default: int = 1) -> str:
    """Print a numbered list and return the chosen key."""
    console.print(f"\n[bold]{title}[/bold]\n")
    table = Table.grid(padding=(0, 2))
    table.add_column(justify="right", width=2)
    table.add_column()
    table.add_column(style="dim")
    for i, option in enumerate(options, start=1):
        marker = "[cyan]" + str(i) + "[/cyan]"
        label = option.label + ("  [dim](recommended)[/dim]" if i == default else "")
        table.add_row(marker, label, f"{option.cost}  {option.detail}".strip())
    console.print(table)
    console.print()
    index = IntPrompt.ask("  Choose", default=default, choices=[str(i) for i in range(1, len(options) + 1)], show_choices=False)
    return options[index - 1].key


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------

RUN_CHOICES = [
    Choice("verified", "Full review + recommendations", "everything, then check it", "~5 min  ~$1.10"),
    Choice("baseline", "Skip the verification loop", "same memo, nothing checks it", "~4 min  ~$0.80"),
    Choice("naive", "Naive — no grounding rules", "the 'before' for the demo", "~3 min  ~$0.60"),
]


def run_options() -> dict[str, Any]:
    """Ask everything `wealth run` needs. Returns kwargs for the run command."""
    mode = choose("What kind of review?", RUN_CHOICES)

    live = False
    if os.getenv("ROBINHOOD_TRADING_MCP_URL"):
        live = Confirm.ask(
            "  Use [bold]live[/bold] Robinhood data (otherwise synthetic fixtures)",
            default=False,
        )
    propose = Confirm.ask(
        "  Have it [bold]propose trades[/bold] for your approval", default=False
    )
    email = None
    default_to = os.getenv("REPORT_EMAIL")
    if Confirm.ask("  Email the report as well", default=False):
        email = Prompt.ask("  Send to", default=default_to) if default_to else Prompt.ask("  Send to")

    return {"mode": mode, "live": live, "propose_trades": propose, "email": email}


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------


@dataclass
class RunEntry:
    """A run you could open."""

    workspace: RunWorkspace
    label: str
    frozen: bool


def list_runs(limit: int = 8) -> list[RunEntry]:
    """Your runs, newest first, then the frozen workshop artifacts."""
    entries: list[RunEntry] = []
    if RUNS_DIR.exists():
        for path in sorted((p for p in RUNS_DIR.iterdir() if p.is_dir()), reverse=True)[:limit]:
            entries.append(RunEntry(RunWorkspace(run_id=path.name), path.name, frozen=False))
    frozen_dir = ARTIFACTS_DIR / "runs"
    if frozen_dir.exists():
        for path in sorted(p for p in frozen_dir.iterdir() if p.is_dir()):
            entries.append(
                RunEntry(
                    RunWorkspace(run_id=path.name, base=frozen_dir),
                    f"{path.name} (frozen)",
                    frozen=True,
                )
            )
    return entries


def choose_run(prompt: str = "Which run?") -> RunWorkspace | None:
    """Show the runs that exist with their scores, and open the one picked."""
    from wealth_agent.verify import verify_memo

    entries = list_runs()
    if not entries:
        console.print("\n[yellow]No runs yet.[/yellow] Try [bold]make run[/bold] first.\n")
        return None

    console.print(f"\n[bold]{prompt}[/bold]\n")
    table = Table.grid(padding=(0, 2))
    table.add_column(justify="right", width=2)
    table.add_column()
    table.add_column(justify="right")
    table.add_column(style="dim")
    for i, entry in enumerate(entries, start=1):
        memo = entry.workspace.read_memo()
        report = verify_memo(memo, entry.workspace) if memo else None
        score = f"{report.score:.0%}" if report else "—"
        colour = "green" if report and report.passed else "yellow" if report else "dim"
        flags = ""
        if report and report.failures:
            flags = f"{len(report.failures)} need review"
        has_report = (entry.workspace.root / "report.html").exists()
        table.add_row(
            f"[cyan]{i}[/cyan]",
            entry.label + ("  [dim]·[/dim] [green]report ready[/green]" if has_report else ""),
            f"[{colour}]{score}[/{colour}]",
            flags,
        )
    console.print(table)
    console.print()
    index = IntPrompt.ask(
        "  Open", default=1, choices=[str(i) for i in range(1, len(entries) + 1)], show_choices=False
    )
    return entries[index - 1].workspace


# --------------------------------------------------------------------------
# the top-level menu
# --------------------------------------------------------------------------

MAIN = [
    Choice("run", "Run a wealth review", "produces a report and opens it", "~5 min  ~$1.10"),
    Choice("report", "Open a previous report", "or one of the frozen demo runs", "free"),
    Choice("compare", "Compare naive vs verified", "the workshop's central result", "free"),
    Choice("demo", "Walk the workshop demo", "one keypress per beat", "free"),
    Choice("cost", "What did the last run cost?", "tokens, cache hits, dollars", "free"),
    Choice("doctor", "Check everything works", "run this before you present", "free"),
]


def main_menu() -> str:
    console.print(
        Panel(
            "A deep agent that reads a brokerage account, six months of card spending,\n"
            "and an investment policy — then tells you what to change, with every\n"
            "number traceable to the tool call it came from.",
            title="wealth",
            border_style="cyan",
            padding=(1, 2),
        )
    )
    return choose("What would you like to do?", MAIN)


__all__ = [
    "Choice",
    "RunEntry",
    "choose",
    "choose_run",
    "console",
    "list_runs",
    "main_menu",
    "run_options",
]
