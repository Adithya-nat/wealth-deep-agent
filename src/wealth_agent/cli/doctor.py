"""Pre-flight. Ninety seconds before you share a screen.

Every check here corresponds to something that has actually gone wrong while
preparing this material: a key that expired, a fixture that got edited, a
prompt whose placeholder no longer matched, a cache that silently stopped
working. The value is not that it passes — it is that when it fails it names
the fix, at a moment when you still have time to apply it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from rich.console import Console
from rich.table import Table

console = Console()


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    fatal: bool = True


def _keys() -> list[Check]:
    checks = []
    for name, needed_for, fatal in (
        ("ANTHROPIC_API_KEY", "running the agent", True),
        ("TAVILY_API_KEY", "market research", False),
        ("LANGSMITH_API_KEY", "tracing", False),
        ("OPENAI_API_KEY", "the Loop 0 judge", False),
        ("RESEND_API_KEY", "emailing reports", False),
    ):
        present = bool(os.getenv(name))
        checks.append(
            Check(
                name,
                present,
                "set" if present else f"not set — needed for {needed_for}",
                fatal=fatal,
            )
        )
    return checks


def _fixtures() -> list[Check]:
    from wealth_agent.config import ARTIFACTS_DIR
    from wealth_agent.data.store import RunWorkspace
    from wealth_agent.verify import verify_memo

    checks = []
    for label in ("naive", "baseline", "verified", "ledger-bug"):
        ws = RunWorkspace(run_id=label, base=ARTIFACTS_DIR / "runs")
        memo = ws.read_memo()
        if not memo:
            checks.append(Check(f"artifact/{label}", False, "missing or empty memo"))
            continue
        report = verify_memo(memo, ws)
        checks.append(
            Check(
                f"artifact/{label}",
                True,
                f"{report.score:.2%} grounded, {report.checked} claims checked",
                fatal=False,
            )
        )
    return checks


def _prompts() -> list[Check]:
    from wealth_agent import prompts

    try:
        names = prompts.names()
        for name in names:
            prompts.get(name)
        return [Check("prompts", True, f"{len(names)} load cleanly", fatal=False)]
    except Exception as exc:  # noqa: BLE001
        return [Check("prompts", False, f"{type(exc).__name__}: {exc}")]


def _policy() -> list[Check]:
    from wealth_agent.policy import PolicyError, load_policy

    try:
        policy = load_policy()
        return [
            Check(
                "policy",
                True,
                f"{policy.name}: {len(policy.sector_targets)} sector targets, "
                f"{policy.drift_band:g}pp band",
                fatal=False,
            )
        ]
    except PolicyError as exc:
        return [Check("policy", False, str(exc))]


def _cache_floors() -> list[Check]:
    """Every priced model needs a documented minimum cacheable prefix.

    Without one you cannot tell whether an agent is caching or silently paying
    full price, and Haiku's floor is four times Sonnet's — on the model you pick
    precisely to save money.
    """
    from wealth_agent.models import MIN_CACHEABLE_TOKENS, PRICES

    missing = [m for m in PRICES if m not in MIN_CACHEABLE_TOKENS]
    return [
        Check(
            "cache floors",
            not missing,
            "documented for every priced model" if not missing else f"missing for {missing}",
            fatal=False,
        )
    ]


def run_doctor() -> int:
    """Run every check. Returns a process exit code."""
    groups = {
        "API keys": _keys(),
        "Prompts": _prompts(),
        "Policy": _policy(),
        "Models": _cache_floors(),
        "Frozen demo artifacts": _fixtures(),
    }

    failures = 0
    for title, checks in groups.items():
        table = Table(title=title, title_justify="left", border_style="dim", show_header=False)
        table.add_column(width=3)
        table.add_column(width=22)
        table.add_column(overflow="fold")
        for check in checks:
            if check.ok:
                mark, style = "[green]✓[/green]", ""
            elif check.fatal:
                mark, style = "[red]✗[/red]", "red"
                failures += 1
            else:
                mark, style = "[yellow]•[/yellow]", "yellow"
            table.add_row(mark, check.name, f"[{style}]{check.detail}[/{style}]" if style else check.detail)
        console.print(table)
        console.print()

    if failures:
        console.print(f"[red]{failures} blocking problem(s).[/red] Fix these before presenting.\n")
        return 1
    console.print("[green]Ready.[/green] Optional items marked • are fine to leave unset.\n")
    return 0


__all__ = ["Check", "run_doctor"]
