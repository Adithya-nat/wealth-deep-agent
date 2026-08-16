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
from wealth_agent.store import RunWorkspace, latest_run
from wealth_agent.verify import Verdict, verify_memo

#: Frozen runs, committed to the repo so every demo step works with the network
#: unplugged. A workshop that depends on a live model call is a workshop that
#: fails in front of an audience.
ARTIFACT_RUNS = ARTIFACTS_DIR / "runs"

console = Console()

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
    from langchain_core.messages import HumanMessage

    from wealth_agent.supervisor import RUBRIC, build_wealth_agent

    settings = _settings(args)
    data_mode = "LIVE — real account" if settings.is_live else "demo fixtures"
    described = {
        "naive": "no skills, no grounding rules, no verification",
        "baseline": "skills + grounding rules, nothing checks them",
        "verified": "verifier subagent + runtime rubric loop",
    }[args.mode]
    console.print(
        Panel(
            f"[bold]{args.question}[/bold]\n\n"
            f"data: {data_mode}\n"
            f"agent: [bold]{args.mode}[/bold] — {described}"
            + ("\nresearcher: weakened (smaller model)" if args.weak else ""),
            title="wealth run",
            border_style="cyan",
        )
    )

    evaluations: list[dict[str, Any]] = []

    def on_evaluation(ev: dict[str, Any]) -> None:
        evaluations.append(ev)
        colour = {"satisfied": "green", "needs_revision": "yellow"}.get(
            str(ev.get("result")), "red"
        )
        console.print(
            f"  [{colour}]rubric iteration {ev.get('iteration')}: "
            f"{ev.get('result')}[/{colour}] — {ev.get('explanation', '')[:160]}"
        )

    bundle = await build_wealth_agent(
        mode=args.mode,
        settings=settings,
        weak_researcher=args.weak,
        on_rubric_evaluation=on_evaluation if args.mode == "verified" else None,
    )
    console.print(f"workspace: [dim]{bundle.workspace.root}[/dim]\n")

    payload: dict[str, Any] = {"messages": [HumanMessage(args.question)]}
    if bundle.verified:
        payload["rubric"] = RUBRIC

    result = await bundle.agent.ainvoke(
        payload,
        config={"configurable": {"thread_id": bundle.workspace.run_id}},
        # Long runs with several subagents blow through the default ceiling
        # well before they are actually stuck.
        recursion_limit=args.recursion_limit,
    )

    memo = _resolve_memo(bundle.workspace, result)
    console.print(Panel(memo or "(no memo produced)", title="memo", border_style="green"))
    _print_verification(bundle.workspace)
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
# auth / mcp
# --------------------------------------------------------------------------


def _auth(args: argparse.Namespace) -> int:
    from wealth_agent.config import BANKING_MCP_URL, TRADING_MCP_URL
    from wealth_agent.mcp_auth import FileTokenStorage, callback_port_is_free, logout
    from wealth_agent.mcp_clients import BANKING, TRADING

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

    from wealth_agent.mcp_auth import build_oauth_provider

    for name, url in targets.items():
        console.print(f"\n[bold]{name}[/bold] → {url}")
        client = MultiServerMCPClient(
            {name: {"transport": "streamable_http", "url": url,
                    "auth": build_oauth_provider(name, url)}}
        )
        try:
            tools = await client.get_tools(server_name=name)
        except Exception as exc:  # noqa: BLE001 — surface the real reason
            console.print(f"  [red]failed:[/red] {type(exc).__name__}: {exc}")
            continue
        console.print(f"  [green]connected[/green] — {len(tools)} tools")
    return 0


def _mcp_probe(args: argparse.Namespace) -> int:
    return asyncio.run(_probe(args))


async def _probe(args: argparse.Namespace) -> int:
    from wealth_agent.mcp_clients import BANKING, TRADING, build_client, partition_tools

    settings = _settings(args)
    client = build_client(settings)
    console.print(f"mode: {'LIVE' if settings.is_live else 'replay fixtures'}\n")

    surface: dict[str, Any] = {}
    for server in (TRADING, BANKING):
        try:
            tools = await client.get_tools(server_name=server)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]{server}: {type(exc).__name__}: {exc}[/red]")
            continue
        split = partition_tools(tools)
        table = Table(title=server, border_style="cyan")
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
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
