"""The workshop run-of-show, as code.

A facilitator script in a markdown file drifts from the commands it describes
the first time a flag changes, and you find out in front of the room. Here the
script *is* the commands: each beat prints its talking point, waits for a
keypress, runs the thing, and moves on.

Every beat runs offline against frozen artifacts. Nothing here needs a key, a
network, or a model call — which is the property you want when the venue wifi
turns out to be a captive portal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

console = Console()


@dataclass
class Beat:
    """One segment of the session."""

    minutes: str
    title: str
    say: str
    run: Callable[[], object] | None = None
    then: str = ""


def _compare() -> int:
    import argparse

    from wealth_agent.cli.app import _compare as compare

    return compare(argparse.Namespace())


def _report(label: str) -> Callable[[], int]:
    def go() -> int:
        import argparse

        from wealth_agent.cli.app import _report as report

        return report(
            argparse.Namespace(
                run=None, artifact=label, mode=label, email=None, no_open=False
            )
        )

    return go


def _inspect(label: str) -> Callable[[], int]:
    def go() -> int:
        import argparse

        from wealth_agent.cli.app import _inspect as inspect

        return inspect(argparse.Namespace(run=None, artifact=label))

    return go


def _verify(label: str) -> Callable[[], int]:
    def go() -> int:
        import argparse

        from wealth_agent.cli.app import _verify as verify

        return verify(argparse.Namespace(run=None, artifact=label))

    return go


def _self_test() -> int:
    import argparse

    from wealth_agent.cli.app import _evals

    return _evals(argparse.Namespace(action="self-test"))


BEATS: list[Beat] = [
    Beat(
        "0:00", "The ship gate",
        "This is a wealth memo an agent wrote. Compliance signs off Monday. You are "
        "the tech lead — what do you need before you say yes?\n\n"
        "Take answers. Someone says 'check the numbers.' Ask how. There are 66 "
        "figures and about twenty external claims.",
        _report("naive"),
        "Scroll to the recommended actions. Then land on citations: ZERO.",
    ),
    Beat(
        "0:02", "The after",
        "Same account, same data, the disciplined agent. Recommendations with "
        "dollar amounts that Python computed, market context with source ids, "
        "and every figure on the page clickable back to the tool that produced "
        "it.\n\n"
        "100% grounded. 86 claims checked automatically, zero needing a human. "
        "$0.69 and under four minutes.",
        _report("recommended"),
        "Click a number. That is the whole thesis in one gesture.",
    ),
    Beat(
        "0:06", "The measured result",
        "Three configurations. Same subagents, same tools, same data, same question.",
        _compare,
        "The naive memo is the best-written of the three and the least defensible. "
        "That is the whole session in one table.",
    ),
    Beat(
        "0:08", "The architecture",
        "Open src/wealth_agent/agents/ — one file per agent. Three deep, two "
        "deliberately not. A subagent is a context window with a job, and the "
        "harness follows from the job rather than from habit.",
        None,
        "ls src/wealth_agent/agents/  ·  then open verifier.py and "
        "allocation_strategist.py side by side.",
    ),
    Beat(
        "0:13", "What the agent actually observed",
        "The ledger is the evidence. Every tool result, recorded by middleware as it "
        "arrived — not by the tools, so no future tool can forget to participate.",
        _inspect("baseline"),
        "Then open middleware/grounding_ledger.py. 'You cannot evaluate what you did "
        "not record.'",
    ),
    Beat(
        "0:19", "The bug I shipped",
        "79% grounded. Point at $18,420.55 reported as unsupported — that is the real "
        "cash balance, straight out of get_account_balances. So why did my own "
        "verifier call it unsupported?\n\n"
        "Let them think. Then: the recording middleware was on the supervisor only. "
        "Declarative subagents compile their own stack and inherit nothing.",
        _verify("ledger-bug"),
        "One-line fix took it to 99.24%. The lesson generalizes: in a multi-agent "
        "system, cross-cutting concerns have to be installed per agent.",
    ),
    Beat(
        "0:26", "Where deterministic checking runs out",
        "Twenty labeled memos. Before grading anything with a model, check the labels "
        "against the deterministic checker. It agrees on 18 of 20 — and the two "
        "misses are the most useful part of the session.",
        _self_test,
        "u06: a real number against the wrong denominator. Every digit checks out and "
        "the sentence is false — that is what a judge is for. "
        "u08: a fabricated 12% passes because a real 11.88% rounds to 12. "
        "Tighten the tolerance and honest reformatting starts failing. "
        "No setting avoids both.",
    ),
    Beat(
        "0:33", "Loop 0 — measure the judge before believing it",
        "v1 scored 85% agreement. v2 also scored 85%. By the headline number v2 was a "
        "complete waste of time — it had in fact eliminated the entire dangerous error "
        "class and introduced a milder one.\n\n"
        "A single agreement number would have told you to throw away the change that "
        "mattered most.",
        None,
        "Show prompts/judge/v1.md v2.md v3.md side by side. The prompts are files, so "
        "this is a diff rather than three string literals.",
    ),
    Beat(
        "0:39", "What it costs",
        "Verification is not free and this repo does not pretend otherwise. Show the "
        "cost breakdown per agent, and the cache hit rate.\n\n"
        "Whether the trade is worth making depends entirely on what happens at your "
        "company when a memo is wrong. You can only have that argument if both numbers "
        "are on the table.",
        None,
        "make cost  ·  then the report footer: N claims checked by machine, M need a "
        "human. That ratio is the product.",
    ),
    Beat(
        "0:43", "Where this fits",
        "Before: agent fundamentals, deep agent basics. After: deployment, online evals "
        "and alerting, annotation queues, cost and latency engineering.\n\n"
        "Two things transfer regardless of framework. Record your evidence at the "
        "moment it arrives. And measure your judge before you believe it.",
        None,
    ),
]


def run_demo() -> int:
    """Walk the beats, one keypress at a time."""
    console.print(
        Panel(
            "The workshop run-of-show. Enter advances, [bold]s[/bold] skips a command, "
            "[bold]q[/bold] quits.\nEverything runs offline against frozen artifacts — "
            "no keys, no network, no model calls.",
            title="wealth demo",
            border_style="cyan",
            padding=(1, 2),
        )
    )
    for i, beat in enumerate(BEATS, start=1):
        console.print()
        console.rule(f"[bold cyan]{beat.minutes}[/bold cyan]  {beat.title}", align="left")
        console.print(Panel(beat.say, border_style="dim", padding=(1, 2)))
        answer = Prompt.ask(
            f"  [dim]beat {i}/{len(BEATS)}[/dim]",
            choices=["", "s", "q"],
            default="",
            show_choices=False,
            show_default=False,
        )
        if answer == "q":
            console.print("\n[dim]stopped[/dim]\n")
            return 0
        if beat.run and answer != "s":
            console.print()
            beat.run()
        if beat.then:
            console.print(f"\n  [yellow]→[/yellow] [dim]{beat.then}[/dim]")
    console.print("\n[green]End of the walkthrough.[/green]\n")
    return 0


__all__ = ["BEATS", "Beat", "run_demo"]
