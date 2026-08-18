"""`wealth` — one command per thing you might want to do or show.

Workshop demos fail on typos and half-remembered flags. Every step of the
session is a single subcommand here, and every one of them works offline
against fixtures by default.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from wealth_agent.config import ARTIFACTS_DIR, SETTINGS, Settings
from wealth_agent.data.store import RunWorkspace, latest_run
from wealth_agent.verify import Verdict, verify_memo

#: Frozen runs, committed to the repo so every demo step works with the network
#: unplugged. A workshop that depends on a live model call is a workshop that
#: fails in front of an audience.
ARTIFACT_RUNS = ARTIFACTS_DIR / "runs"

console = Console()


def _quiet_third_party_noise() -> None:
    """Silence log lines that are neither actionable nor ours.

    `mcp.client.streamable_http` warns "Session termination failed: 400" every
    time a server declines the DELETE that closes a session. Robinhood's servers
    always decline it. The session is closed regardless, nothing is wrong, and
    the line appears often enough to bury the output that matters — including,
    on one run, the OAuth URL a human was supposed to click.

    Scoped to that one logger on purpose. Blanket-silencing third-party warnings
    is how you stop hearing about the ones that mean something.
    """
    import logging

    logging.getLogger("mcp.client.streamable_http").setLevel(logging.ERROR)

DEFAULT_QUESTION = (
    "Give me a wealth review for the last three months: how my portfolio is "
    "allocated, where my money went, and any market context I should know "
    "about my two largest holdings."
)


def _settings(args: argparse.Namespace) -> Settings:
    return Settings(
        demo_mode=not getattr(args, "live", False),
        allow_write_tools=getattr(args, "allow_writes", False) or SETTINGS.allow_write_tools,
    )


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------


async def _run(args: argparse.Namespace) -> int:
    """Produce a review, showing the work, then hand back a report.

    The old version invoked the agent and printed the memo. Both halves were
    wrong for a run that takes minutes: `ainvoke` shows nothing until it is
    finished, and a 200-line memo in scrollback is not a deliverable anyone
    would read. This streams the run into a live panel and ends with a rendered
    HTML report open in the browser.
    """
    import time

    from langchain_core.messages import HumanMessage

    from langgraph.types import Command

    from wealth_agent.agents.common import CALL_LIMITS, truncated_agents
    from wealth_agent.agents.supervisor import RUBRIC, build_wealth_agent
    from wealth_agent.cli.approval import ask, pending_from
    from wealth_agent.middleware.verification_gate import MAX_REVISIONS, revision_request
    from wealth_agent.cli.progress import RunProgress, track
    from wealth_agent.reporting.delivery import deliver
    from wealth_agent.reporting.render import build_report_data, write_report

    settings = _settings(args)
    if getattr(args, "propose_trades", False):
        settings = Settings(demo_mode=settings.demo_mode, allow_write_tools=True)
    described = {
        "naive": "no skills, no grounding rules, no verification",
        "baseline": "skills + grounding rules, nothing checks them",
        "verified": "verifier subagent + runtime rubric loop",
    }[args.mode]
    console.print(
        Panel(
            f"[bold]{args.question}[/bold]\n\n"
            f"data: {'LIVE — real account' if settings.is_live else 'demo fixtures'}\n"
            f"agent: [bold]{args.mode}[/bold] — {described}"
            + ("\nresearcher: weakened (smaller model)" if args.weak else ""),
            title="wealth run",
            border_style="cyan",
        )
    )

    bundle = await build_wealth_agent(
        mode=args.mode,
        settings=settings,
        weak_researcher=args.weak,
        always_judge=getattr(args, "always_judge", False),
    )
    for source in bundle.degraded:
        console.print(
            Panel(
                f"[bold]{source.server}[/bold] refused the live connection, so this "
                f"run is using [bold]replay fixtures[/bold] for it.\n\n"
                f"[dim]{source.fallback_reason}[/dim]\n\n"
                f"Any figure sourced from {source.server} in the report below is "
                f"[bold]synthetic[/bold], not your account.",
                title="[bold yellow]degraded to fixtures[/bold yellow]",
                border_style="yellow",
            )
        )
    console.print(f"workspace: [dim]{bundle.workspace.root}[/dim]")

    payload: dict[str, Any] = {"messages": [HumanMessage(args.question)]}
    if bundle.verified:
        payload["rubric"] = RUBRIC

    started = time.monotonic()
    config = {"configurable": {"thread_id": bundle.workspace.run_id}}
    progress = RunProgress()

    # One loop, two reasons to go round again.
    #
    #   1. The run paused on a write tool and a human has to answer.
    #   2. Deterministic verification failed and the agent gets the specific
    #      findings back to fix.
    #
    # Both continue the same thread, so the agent keeps everything it had. The
    # verification half lives here rather than in middleware because
    # `after_agent` cannot restart a finished agent — see `revision_request`.
    next_input: Any = payload
    revisions = 0
    while True:
        stream = bundle.agent.astream(
            next_input,
            config=config,
            stream_mode=["updates", "custom"],
            # Without this, a subagent's events never reach this stream and the
            # panel shows the supervisor doing everything by itself.
            subgraphs=True,
            recursion_limit=args.recursion_limit,
        )
        progress = await track(
            stream, title=f"wealth run · {args.mode}", console=console, progress=progress
        )

        state = await bundle.agent.aget_state(config)
        pending = pending_from(getattr(state, "interrupts", ()) or ())
        if pending:
            next_input = Command(resume={"decisions": ask(pending)})
            continue

        if not bundle.verified or revisions >= MAX_REVISIONS:
            break
        _report, request = revision_request(bundle.workspace, revisions + 1)
        if request is None:
            break
        revisions += 1
        next_input = {"messages": [request]}

    elapsed = time.monotonic() - started

    memo = bundle.workspace.read_memo()
    if not memo:
        console.print("[red]The run produced no memo.[/red]")
        return 1

    # Written before the report so the run is auditable even if rendering fails.
    (bundle.workspace.root / "cost.json").write_text(
        json.dumps({**bundle.meter.to_json(), "elapsed_seconds": round(elapsed, 1)}, indent=2),
        encoding="utf-8",
    )

    data = build_report_data(
        bundle.workspace,
        mode=args.mode,
        meter=bundle.meter,
        elapsed_seconds=elapsed,
        truncated=truncated_agents(bundle.meter),
    )
    report_path = write_report(data)
    result = deliver(
        report_path,
        open_browser=not getattr(args, "no_open", False),
        email_to=getattr(args, "email", None),
    )

    truncated = truncated_agents(bundle.meter)
    if truncated:
        console.print(
            Panel(
                "[bold]This run did not finish.[/bold]\n\n"
                + "\n".join(
                    f"  · [bold]{name}[/bold] reached its ceiling of "
                    f"{CALL_LIMITS.get(name)} model calls and was stopped mid-task."
                    for name in truncated
                )
                + "\n\nThe memo below is incomplete, and its grounding score only "
                "describes the part that got written. Do not read it as a finished "
                "review.\n\n[dim]Raise the ceiling in agents/common.py, or find out "
                "why the agent is looping.[/dim]",
                title="[bold red]truncated[/bold red]",
                border_style="red",
            )
        )

    verification = data.verification
    colour = "green" if verification.passed else "yellow"

    # Say out loud that verification ran, how many times, and how it ended.
    # "verified" is a mode name until the run shows its working.
    if bundle.verified:
        if verification.passed:
            console.print(
                "\n  [green]✓ verified[/green] — the deterministic check passed"
                + (f" after {revisions} revision(s)" if revisions else " first time")
            )
        else:
            console.print(
                f"\n  [yellow]⚠ not fully verified[/yellow] — "
                f"{len(verification.failures)} claim(s) still failing after "
                f"{revisions} revision(s). They are flagged in the report."
            )

    console.print(
        f"  [{colour}]{verification.score:.1%} grounded[/{colour}]"
        f"  ·  {verification.checked} claims checked"
        f"  ·  [bold]{len(verification.failures)}[/bold] need a human"
    )
    console.print(
        f"  {elapsed / 60:.1f} min  ·  {progress.tokens / 1000:,.0f}k tokens"
        f"  ·  [bold]${bundle.meter.cost():.2f}[/bold]"
        f"  ·  {bundle.meter.cache_hit_rate:.0%} served from cache"
    )
    from wealth_agent.voice import lint as voice_lint

    voice = voice_lint(memo)
    if voice:
        console.print(
            f"  [dim]{len(voice)} phrase(s) read as machine-written "
            f"— see the report. Style only; grounding is unaffected.[/dim]"
        )

    console.print(f"  report: [bold]{report_path}[/bold]" + ("  [dim](opened)[/dim]" if result.opened else ""))
    if result.emailed_to:
        console.print(f"  emailed to [bold]{result.emailed_to}[/bold]")
    elif result.email_error:
        console.print(f"  [yellow]email not sent — {result.email_error}[/yellow]")
    console.print(f"\n[dim]run id: {bundle.workspace.run_id}[/dim]")
    return 0


def _resolve_memo(ws: RunWorkspace, result: dict[str, Any]) -> str:
    """The memo is the file the agent wrote, not the last thing it said.

    The verified agent narrates: it reports the verifier's findings, then
    presents the revised memo. Treating that whole message as the memo drags
    the commentary into verification — and the commentary *quotes figures it
    just removed*, so the agent gets penalized for correctly describing its own
    fix. The deliverable is `/memo.md`; the chat message is a cover letter.
    """
    written = ws.read_memo().strip()
    if written:
        return written
    memo = _final_text(result)
    if memo:
        ws.write_memo(memo)
    return memo


def _final_text(result: dict[str, Any]) -> str:
    for message in reversed(result.get("messages", [])):
        if getattr(message, "type", None) != "ai":
            continue
        content = message.content
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            text = "\n".join(
                block["text"]
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ).strip()
            if text:
                return text
    return ""


# --------------------------------------------------------------------------
# verify / inspect
# --------------------------------------------------------------------------


def _print_verification(ws: RunWorkspace) -> None:
    memo = ws.read_memo()
    if not memo:
        console.print("[yellow]no memo to verify[/yellow]")
        return
    report = verify_memo(memo, ws)

    colour = "green" if report.passed else "red"
    table = Table(title="deterministic verification", border_style=colour)
    table.add_column("metric")
    table.add_column("value", justify="right")
    table.add_row("grounding score", f"{report.score:.2%}")
    table.add_row("citations checked", str(report.checked_citations))
    table.add_row("figures checked", str(report.checked_figures))
    table.add_row("unsupported", str(sum(1 for f in report.failures if f.verdict is Verdict.UNSUPPORTED)))
    table.add_row("fabricated", f"[bold red]{len(report.fabricated)}[/bold red]")
    table.add_row("passed", f"[{colour}]{report.passed}[/{colour}]")
    console.print(table)

    for finding in report.failures[:15]:
        style = "red" if finding.verdict is Verdict.FABRICATED else "yellow"
        console.print(
            f"  [{style}]{finding.verdict}[/{style}] line {finding.line}: {finding.detail}"
        )
        console.print(f"    [dim]{finding.excerpt[:140]}[/dim]")


def _resolve_workspace(args: argparse.Namespace) -> RunWorkspace | None:
    """Open a run by artifact label, run id, or most-recent.

    Artifacts come first: during a live session you want the frozen, known-good
    run, not whatever happened to execute last.
    """
    if getattr(args, "artifact", None):
        return RunWorkspace(run_id=args.artifact, base=ARTIFACT_RUNS)
    if getattr(args, "run", None):
        return RunWorkspace(run_id=args.run)
    return latest_run()


def _verify(args: argparse.Namespace) -> int:
    ws = _resolve_workspace(args)
    if ws is None:
        console.print("[red]no runs found. Try `wealth run` first.[/red]")
        return 1
    console.print(f"verifying [bold]{ws.run_id}[/bold]")
    _print_verification(ws)
    return 0


def _artifacts(args: argparse.Namespace) -> int:
    """Freeze a run so the demo never depends on a live model call."""
    import shutil

    if args.action == "list":
        table = Table(title="cached runs", border_style="cyan")
        table.add_column("label")
        table.add_column("grounding", justify="right")
        table.add_column("citations", justify="right")
        table.add_column("fabricated", justify="right")
        for path in sorted(ARTIFACT_RUNS.glob("*")) if ARTIFACT_RUNS.exists() else []:
            if not path.is_dir():
                continue
            ws = RunWorkspace(run_id=path.name, base=ARTIFACT_RUNS)
            memo = ws.read_memo()
            report = verify_memo(memo, ws) if memo else None
            table.add_row(
                path.name,
                f"{report.score:.2%}" if report else "—",
                str(report.checked_citations) if report else "—",
                str(len(report.fabricated)) if report else "—",
            )
        console.print(table)
        return 0

    source = RunWorkspace(run_id=args.run).root if args.run else (latest_run() or RunWorkspace()).root
    if not source.exists():
        console.print(f"[red]{source} does not exist[/red]")
        return 1
    dest = ARTIFACT_RUNS / args.label
    if dest.exists():
        shutil.rmtree(dest)
    # Skills and memory are copies of repo files; re-committing them per run
    # triples the artifact size for nothing.
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns("skills", "AGENTS.md"))
    console.print(f"froze [bold]{source.name}[/bold] → [green]{dest}[/green]")
    console.print(f"replay it with: [bold]wealth verify --artifact {args.label}[/bold]")
    return 0


def _inspect(args: argparse.Namespace) -> int:
    ws = _resolve_workspace(args)
    if ws is None:
        console.print("[red]no runs found.[/red]")
        return 1
    console.print(Panel(json.dumps(ws.describe(), indent=2), title="run", border_style="cyan"))

    table = Table(title="ledger", border_style="cyan")
    table.add_column("agent")
    table.add_column("tool")
    table.add_column("chars", justify="right")
    for entry in ws.ledger.entries():
        table.add_row(entry.agent, entry.name, str(len(entry.content)))
    console.print(table)

    sources = ws.sources()
    if sources:
        stable = Table(title="sources", border_style="cyan")
        stable.add_column("id")
        stable.add_column("title")
        stable.add_column("url", overflow="fold")
        for source in sources.values():
            stable.add_row(source.id, source.title[:48], source.url)
        console.print(stable)
    return 0



# --------------------------------------------------------------------------
# report / cost / compare / doctor / menu
# --------------------------------------------------------------------------


def _report(args: argparse.Namespace) -> int:
    """Render a report from any run, with zero model calls.

    This is how the report design gets iterated on for free, and it is the
    demo-safe path when the network is gone: every frozen artifact can produce
    its report offline.
    """
    from wealth_agent.cli.menu import choose_run
    from wealth_agent.reporting.delivery import deliver
    from wealth_agent.reporting.render import build_report_data, write_report

    ws = _resolve_workspace(args) if (args.run or args.artifact) else choose_run("Which report?")
    if ws is None:
        return 1
    if not ws.read_memo():
        console.print(f"[yellow]{ws.run_id} has no memo to report on.[/yellow]")
        return 1

    path = write_report(build_report_data(ws, mode=args.mode or "verified"))
    result = deliver(path, open_browser=not args.no_open, email_to=args.email)
    console.print(f"\n  report: [bold]{path}[/bold]" + ("  [dim](opened)[/dim]" if result.opened else ""))
    if result.emailed_to:
        console.print(f"  emailed to [bold]{result.emailed_to}[/bold]")
    elif result.email_error:
        console.print(f"  [yellow]email not sent — {result.email_error}[/yellow]")
    return 0


def _cost(args: argparse.Namespace) -> int:
    """What a run cost, and — the number that matters — how much was cached."""
    import json

    ws = _resolve_workspace(args)
    if ws is None:
        console.print("[red]no runs found.[/red]")
        return 1
    path = ws.root / "cost.json"
    if not path.exists():
        console.print(
            f"[yellow]{ws.run_id} has no cost record.[/yellow] "
            "Only runs made after the cost meter landed have one."
        )
        return 1

    data = json.loads(path.read_text(encoding="utf-8"))
    table = Table(title=f"cost · {ws.run_id}", border_style="cyan")
    for column, justify in (
        ("agent", "left"), ("model", "left"), ("calls", "right"),
        ("input", "right"), ("cached", "right"), ("output", "right"), ("cost", "right"),
    ):
        table.add_column(column, justify=justify)
    for agent, usage in data["by_agent"].items():
        table.add_row(
            agent, usage["model"], str(usage["calls"]),
            f"{usage['input_tokens']:,}", f"{usage['cache_read']:,}",
            f"{usage['output_tokens']:,}", f"${usage['cost_usd']:.3f}",
        )
    console.print(table)

    rate = data["cache_hit_rate"]
    colour = "green" if rate > 0.5 else "yellow" if rate > 0.1 else "red"
    console.print(
        f"\n  total: [bold]{data['total_tokens']:,}[/bold] tokens  ·  "
        f"[bold]${data['total_cost_usd']:.2f}[/bold]  ·  "
        f"[{colour}]{rate:.0%} served from cache[/{colour}]"
    )
    if rate < 0.1:
        console.print(
            "  [yellow]A cache hit rate this low means caching is not working.[/yellow]\n"
            "  [dim]Either a prefix changes between turns, or it is below the model's\n"
            "  minimum cacheable size — see MIN_CACHEABLE_TOKENS in models.py.[/dim]"
        )
    return 0


def _compare(args: argparse.Namespace) -> int:
    """The workshop's central result, offline and free."""
    from wealth_agent.config import ARTIFACTS_DIR
    from wealth_agent.verify import Verdict, verify_memo

    described = {
        "naive": "no skills, no grounding rules, no verification",
        "baseline": "skills + rules, nothing checks them",
        "verified": "+ verifier subagent and runtime rubric",
        "ledger-bug": "the middleware bug this repo caught in itself",
    }
    table = Table(title="same subagents, same tools, same data, same question", border_style="cyan")
    table.add_column("configuration")
    table.add_column("grounding", justify="right")
    table.add_column("citations", justify="right")
    table.add_column("unsupported", justify="right")
    table.add_column("fabricated", justify="right")
    table.add_column("ships?", justify="center")
    for label in ("naive", "baseline", "verified", "ledger-bug"):
        ws = RunWorkspace(run_id=label, base=ARTIFACTS_DIR / "runs")
        memo = ws.read_memo()
        if not memo:
            continue
        report = verify_memo(memo, ws)
        unsupported = sum(1 for f in report.failures if f.verdict is Verdict.UNSUPPORTED)
        cites = report.checked_citations
        table.add_row(
            f"[bold]{label}[/bold]\n[dim]{described.get(label, '')}[/dim]",
            f"{report.score:.2%}",
            f"[red]{cites}[/red]" if cites == 0 else str(cites),
            str(unsupported),
            f"[bold red]{len(report.fabricated)}[/bold red]" if report.fabricated else "0",
            "[green]yes[/green]" if report.passed else "[red]no[/red]",
        )
    console.print(table)
    console.print(
        "\n  [dim]The naive memo is the one to sit with: it reads best of the three and\n"
        "  attributes nothing. The most persuasive one was the least defensible,\n"
        "  and reading it carefully would not have told you.[/dim]\n"
    )
    return 0


def _doctor(args: argparse.Namespace) -> int:
    from wealth_agent.cli.doctor import run_doctor

    return run_doctor()


def _demo() -> int:
    from wealth_agent.cli.demo import run_demo

    return run_demo()


def _menu(args: argparse.Namespace) -> int:
    """The zero-knowledge entry point: pick a thing, get asked what it needs."""
    from wealth_agent.cli.menu import main_menu, run_options

    action = main_menu()
    if action == "run":
        options = run_options()
        return asyncio.run(
            _run(
                argparse.Namespace(
                    question=DEFAULT_QUESTION,
                    mode=options["mode"],
                    live=options["live"],
                    allow_writes=False,
                    weak=False,
                    recursion_limit=150,
                    email=options["email"],
                    no_open=False,
                    always_judge=False,
                    propose_trades=options["propose_trades"],
                )
            )
        )
    if action == "report":
        return _report(argparse.Namespace(run=None, artifact=None, mode=None, email=None, no_open=False))
    if action == "compare":
        return _compare(args)
    if action == "cost":
        return _cost(argparse.Namespace(run=None, artifact=None))
    if action == "doctor":
        return _doctor(args)
    if action == "demo":
        return _demo()
    return 0


# --------------------------------------------------------------------------
# auth / mcp
# --------------------------------------------------------------------------


def _auth(args: argparse.Namespace) -> int:
    from wealth_agent.config import BANKING_MCP_URL, TRADING_MCP_URL
    from wealth_agent.mcp_servers.auth import FileTokenStorage, callback_port_is_free, logout
    from wealth_agent.mcp_servers.clients import BANKING, TRADING

    servers = {TRADING: TRADING_MCP_URL, BANKING: BANKING_MCP_URL}
    targets = servers if args.server == "all" else {args.server: servers[args.server]}

    if args.action == "status":
        table = Table(title="Robinhood MCP auth", border_style="cyan")
        table.add_column("server")
        table.add_column("token cache")
        table.add_column("state")
        for name in targets:
            storage = FileTokenStorage(name)
            table.add_row(
                name,
                str(storage.path),
                "[green]present[/green]" if storage.path.exists() else "[yellow]none[/yellow]",
            )
        console.print(table)
        console.print(
            f"callback port 8765: "
            f"{'[green]free[/green]' if callback_port_is_free() else '[red]in use[/red]'}"
        )
        return 0

    if args.action == "logout":
        for name in targets:
            console.print(f"removed {logout(name)}")
        return 0

    return asyncio.run(_auth_login(targets))


async def _auth_login(targets: dict[str, str]) -> int:
    from langchain_mcp_adapters.client import MultiServerMCPClient

    from wealth_agent.mcp_servers.auth import build_oauth_provider
    from wealth_agent.mcp_servers.clients import root_cause

    for name, url in targets.items():
        console.print(f"\n[bold]{name}[/bold] → {url}")
        client = MultiServerMCPClient(
            {name: {"transport": "streamable_http", "url": url,
                    "auth": build_oauth_provider(name, url)}}
        )
        try:
            tools = await client.get_tools(server_name=name)
        except Exception as exc:  # noqa: BLE001 — surface the real reason
            reason = root_cause(exc)
            console.print(f"  [red]failed:[/red] {reason}")
            hint = _auth_hint(reason)
            if hint:
                console.print(f"  [yellow]{hint}[/yellow]")
            continue
        console.print(f"  [green]connected[/green] — {len(tools)} tools")
    return 0


def _auth_hint(reason: str) -> str:
    """Translate the two OAuth failures this project actually hits.

    Both are properties of Robinhood's deployment rather than bugs here, and
    both produce error text that does not suggest a next step on its own.
    """
    if "client id not allowed" in reason:
        return (
            "The banking server rejects a *cached* token from a "
            "dynamically-registered client, so the SDK re-runs the browser "
            "authorization. It usually completes silently if you are logged "
            "into Robinhood. Approve the tab if one opens; if you skip it, the "
            "run continues on fixtures for this server rather than dying."
        )
    if "does not match expected" in reason:
        return (
            "The URL you configured is not this server's canonical resource "
            "identifier. Point the *_MCP_URL at the host named in the error "
            "and retry — the SDK enforces RFC 9728 resource matching."
        )
    return ""


def _mcp_probe(args: argparse.Namespace) -> int:
    return asyncio.run(_probe(args))


async def _probe(args: argparse.Namespace) -> int:
    from wealth_agent.mcp_servers.clients import BANKING, TRADING, build_client, load_server_tools

    settings = _settings(args)
    client = build_client(settings)
    console.print(f"mode: {'LIVE' if settings.is_live else 'replay fixtures'}\n")

    # The probe lists tools and never calls one, so it deliberately looks at the
    # *whole* surface including writes — showing what `ALLOW_WRITE_TOOLS=0` is
    # keeping away from the model is the entire point of this command.
    surveying = Settings(demo_mode=settings.demo_mode, allow_write_tools=True)

    surface: dict[str, Any] = {}
    for server in (TRADING, BANKING):
        source = await load_server_tools(server, settings=surveying, client=client)
        if source.fallback_reason:
            console.print(f"[yellow]{server}: live refused — {source.fallback_reason}[/yellow]")
            hint = _auth_hint(source.fallback_reason)
            if hint:
                console.print(f"[yellow]  {hint}[/yellow]")
            console.print("[yellow]  showing the fixture surface instead.[/yellow]")
        split = source.split
        tools = split.all
        table = Table(
            title=f"{server}  [{'live' if source.live else 'fixtures'}]",
            border_style="cyan" if source.live or not settings.is_live else "yellow",
        )
        table.add_column("tool")
        table.add_column("access")
        table.add_column("description", overflow="fold")
        for tool in sorted(tools, key=lambda t: t.name):
            access = "write" if tool in split.write else "read"
            table.add_row(
                tool.name,
                f"[red]{access}[/red]" if access == "write" else access,
                (tool.description or "").split("\n")[0][:80],
            )
        console.print(table)
        surface[server] = [
            {"name": t.name, "description": t.description, "schema": t.args_schema}
            for t in tools
        ]

    if args.out:
        from pathlib import Path

        Path(args.out).write_text(json.dumps(surface, indent=2, default=str), encoding="utf-8")
        console.print(f"\nwrote {args.out}")
    return 0


# --------------------------------------------------------------------------
# evals
# --------------------------------------------------------------------------


def _evals(args: argparse.Namespace) -> int:
    if args.action == "self-test":
        import tempfile
        from pathlib import Path

        from wealth_agent.evals.judge_alignment import self_test

        result = self_test(Path(tempfile.mkdtemp()))
        colour = "green" if result["ok"] else "red"
        console.print(Panel(json.dumps(result, indent=2), title="fixture self-test", border_style=colour))
        return 0 if result["ok"] else 1

    if args.action == "judge-align":
        return asyncio.run(_judge_align(args))

    if args.action == "dataset":
        from wealth_agent.evals.experiment import push_judge_dataset, push_memo_dataset

        console.print(f"memo dataset:  [green]{push_memo_dataset()}[/green]")
        console.print(f"judge dataset: [green]{push_judge_dataset()}[/green]")
        return 0

    if args.action in {"naive", "baseline", "verified"}:
        from wealth_agent.evals.experiment import run_memo_experiment

        console.print(
            f"running the [bold]{args.action}[/bold] experiment "
            f"({'weak researcher' if args.weak else 'normal researcher'})..."
        )
        results = run_memo_experiment(
            mode=args.action,
            weak_researcher=args.weak,
            max_concurrency=args.concurrency,
        )
        console.print(results)
        return 0

    return 1


async def _judge_align(args: argparse.Namespace) -> int:
    import tempfile
    from pathlib import Path

    from wealth_agent.evals.evaluators import JUDGE_PROMPT_VERSIONS
    from wealth_agent.evals.judge_alignment import measure_alignment, self_test

    base = Path(tempfile.mkdtemp())
    check = self_test(base)
    if not check["ok"]:
        console.print(f"[red]fixture self-test failed: {check}[/red]")
        return 1
    console.print(
        "[dim]fixture self-test passed — labels agree with the deterministic "
        "checker except the two known semantic misses.[/dim]\n"
    )

    versions = JUDGE_PROMPT_VERSIONS[:1] if args.naive else JUDGE_PROMPT_VERSIONS

    results = []
    for label, prompt in versions:
        console.print(f"measuring [bold]{label}[/bold] over 20 labeled memos...")
        result = await measure_alignment(prompt, label, base=base, model=args.model)
        results.append(result)
        colour = (
            "green" if result.agreement >= 0.9
            else "yellow" if result.agreement >= 0.75
            else "red"
        )
        console.print(Panel(result.summary(), border_style=colour))

    if len(results) > 1:
        table = Table(title="judge alignment across prompt versions", border_style="cyan")
        table.add_column("prompt")
        table.add_column("agreement", justify="right")
        table.add_column("false accepts", justify="right")
        table.add_column("false rejects", justify="right")
        for r in results:
            table.add_row(
                r.label,
                f"{r.agreement:.0%}",
                f"[red]{len(r.false_accepts)}[/red]" if r.false_accepts else "0",
                str(len(r.false_rejects)),
            )
        console.print(table)
        console.print(
            "[dim]Read the columns, not the headline. A single agreement number "
            "hides which way the judge is wrong — and a judge that blesses bad "
            "memos fails differently from one that flags good ones.[/dim]"
        )
    return 0


# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wealth", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Produce a wealth memo.")
    run.add_argument("question", nargs="?", default=DEFAULT_QUESTION)
    run.add_argument(
        "--mode",
        default="baseline",
        choices=["naive", "baseline", "verified"],
        help=(
            "naive: no skills or grounding rules — what most teams ship. "
            "baseline: skills + rules, nothing checks them. "
            "verified: adds the verifier subagent and runtime rubric loop."
        ),
    )
    run.add_argument(
        "--verified",
        dest="mode",
        action="store_const",
        const="verified",
        help="Shorthand for --mode verified.",
    )
    run.add_argument("--weak", action="store_true", help="Run the researcher on a smaller model.")
    run.add_argument("--live", action="store_true", help="Use real Robinhood MCP servers.")
    run.add_argument("--allow-writes", action="store_true", help="Expose gated write tools.")
    run.add_argument("--recursion-limit", type=int, default=150)
    run.set_defaults(func=lambda a: asyncio.run(_run(a)))

    run.add_argument(
        "--propose-trades",
        action="store_true",
        help=(
            "Let the agent propose the trades it recommends. Every one stops at a "
            "human approval prompt; nothing reaches a broker without you."
        ),
    )
    run.add_argument(
        "--always-judge",
        action="store_true",
        help=(
            "Also run the LLM rubric loop. Off by default: the free deterministic "
            "gate answers the same question. Turn it on to demo the pattern."
        ),
    )
    run.add_argument("--email", help="Also email the report to this address.")
    run.add_argument("--no-open", action="store_true", help="Do not open the report in a browser.")

    menu = sub.add_parser("menu", help="Interactive menu. Start here if unsure.")
    menu.set_defaults(func=_menu)

    report = sub.add_parser("report", help="Render and open a report from any run.")
    report.add_argument("--run", help="Run id. Omit to pick from a list.")
    report.add_argument("--artifact", help="Frozen run label.")
    report.add_argument("--mode", help="Label the report with this mode.")
    report.add_argument("--email", help="Also email it.")
    report.add_argument("--no-open", action="store_true")
    report.set_defaults(func=_report)

    cost = sub.add_parser("cost", help="What a run cost, and how much was cached.")
    cost.add_argument("--run", help="Run id. Defaults to the most recent.")
    cost.add_argument("--artifact", help="Frozen run label.")
    cost.set_defaults(func=_cost)

    compare = sub.add_parser("compare", help="naive vs baseline vs verified. Offline.")
    compare.set_defaults(func=_compare)

    doctor = sub.add_parser("doctor", help="Pre-flight check before presenting.")
    doctor.set_defaults(func=_doctor)

    demo = sub.add_parser("demo", help="The workshop walkthrough, one keypress per beat.")
    demo.set_defaults(func=lambda a: _demo())

    verify = sub.add_parser("verify", help="Re-verify a run's memo.")
    verify.add_argument("--run", help="Run id. Defaults to the most recent.")
    verify.add_argument("--artifact", help="Frozen run label, e.g. naive/baseline/verified.")
    verify.set_defaults(func=_verify)

    inspect = sub.add_parser("inspect", help="Show a run's workspace and ledger.")
    inspect.add_argument("--run", help="Run id. Defaults to the most recent.")
    inspect.add_argument("--artifact", help="Frozen run label.")
    inspect.set_defaults(func=_inspect)

    artifacts = sub.add_parser("artifacts", help="Freeze runs for offline demos.")
    artifacts.add_argument("action", choices=["save", "list"])
    artifacts.add_argument("--run", help="Run id to freeze. Defaults to the most recent.")
    artifacts.add_argument("--label", default="run", help="Name to freeze it under.")
    artifacts.set_defaults(func=_artifacts)

    auth = sub.add_parser("auth", help="Robinhood MCP OAuth.")
    auth.add_argument("action", choices=["login", "status", "logout"])
    auth.add_argument("--server", default="all", choices=["all", "robinhood_trading", "robinhood_banking"])
    auth.set_defaults(func=_auth)

    mcp = sub.add_parser("mcp", help="Inspect the MCP tool surface.")
    mcp.add_argument("action", choices=["probe"])
    mcp.add_argument("--live", action="store_true")
    mcp.add_argument("--out", help="Write the surface to this JSON file.")
    mcp.set_defaults(func=_mcp_probe)

    evals = sub.add_parser("evals", help="Datasets, experiments, and judge alignment.")
    evals.add_argument(
        "action",
        choices=["self-test", "judge-align", "dataset", "naive", "baseline", "verified"],
        help=(
            "self-test: check fixture labels against the deterministic checker. "
            "judge-align: Loop 0. dataset: push datasets to LangSmith. "
            "baseline/verified: run an experiment."
        ),
    )
    evals.add_argument("--naive", action="store_true", help="judge-align: naive prompt only.")
    evals.add_argument("--weak", action="store_true", help="Weaken the researcher.")
    evals.add_argument("--model", default="openai:gpt-5.5", help="Judge model.")
    evals.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="Parallel agent runs during an experiment.",
    )
    evals.set_defaults(func=_evals)

    return parser


def main(argv: list[str] | None = None) -> int:
    _quiet_third_party_noise()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
