"""The supervisor deep agent — every harness decision in one readable place.

Read this file top to bottom and you have the architecture. That is the point
of `create_deep_agent`: the harness is configuration, so the interesting part of
an agent system becomes the *choices*, not the plumbing.

The choices worth defending, in the order they appear below:

* **A real directory as the filesystem.** Subagents and the verifier share one
  view with no state-propagation puzzle, runs survive a crash, and you can put
  the agent's entire working memory on a screen with `ls`. The cost is real
  filesystem access, which is why `virtual_mode=True` is not optional.
* **Skills over a longer prompt.** The category rulebook and the memo format are
  hundreds of lines the agent needs *sometimes*. Skills load their frontmatter
  at startup and their body on demand, so those lines cost nothing on the turns
  that do not need them.
* **Ledger middleware, not ledger-writing tools.** No tool can forget to
  participate in an audit trail it does not know about.
* **Permissions that make evidence append-only.** An agent that can rewrite
  `/sources/` can make any claim verify. The ledger is only evidence if the
  thing being audited cannot edit it.
* **Interrupts on every write tool.** Trading is gated on a human, always,
  independent of whether the tool is even visible.
* **A verifier that is not a deep agent.** See `subagents.py`.

`build_wealth_agent(verified=...)` builds both configurations the workshop
compares. The unverified one is not a strawman — it is what a competent team
ships, and it is genuinely good until you ask whether its numbers are real.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from typing import Any

from deepagents import (
    FilesystemPermission,
    RubricMiddleware,
    create_deep_agent,
)
from deepagents.backends import FilesystemBackend
from langchain.agents.middleware import AgentMiddleware, TodoListMiddleware
from langgraph.checkpoint.memory import InMemorySaver

from wealth_agent.config import REPO_ROOT, SETTINGS, Settings
from wealth_agent.middleware.grounding_ledger import GroundingLedgerMiddleware
from wealth_agent.middleware.verification_gate import VerificationGateMiddleware
from wealth_agent.mcp_servers.clients import (
    BANKING,
    TRADING,
    ServerTools,
    build_client,
    load_server_tools,
)
from wealth_agent.data.adapters import resolve_capability_tools
from wealth_agent.data.store import LEDGER_FILE, SOURCES_DIR, RunWorkspace
from wealth_agent import prompts
from wealth_agent.agents.common import call_limit, retry_middleware
from wealth_agent.agents.registry import (
    build_allocation_strategist,
    build_analyst_subagents,
    build_verifier_subagent,
)
from wealth_agent.policy import Policy, load_policy
from wealth_agent.tools.allocation import build_allocation_tools
from wealth_agent.tools import (
    build_portfolio_tools,
    build_research_tools,
    build_spend_tools,
)
from wealth_agent.tools.verification import build_verification_tools
from wealth_agent.models import (
    REASONING_MODEL,
    WEAK_RESEARCHER_MODEL,
    WORKING_MODEL,
    CostMeterMiddleware,
    RunMeter,
    build_model,
)

#: The three configurations the workshop compares.
#:
#: `naive` is not a strawman. It is a competent agent: same subagents, same
#: tools, same data, and a prompt any good engineer would write. What it lacks
#: is the *discipline* — no memo-format skill telling it to cite source ids and
#: never round, no verifier, no runtime grading. It produces a memo that reads
#: better than the verified one, which is exactly why this is the interesting
#: baseline. It still records to the ledger, so we can measure it even though
#: nobody built it to be measured.
MODES = ("naive", "baseline", "verified")

#: Prompts live in `prompts/*.md`, not in this file. The workshop's central
#: comparison is `diff prompts/supervisor.md prompts/supervisor_naive.md` — two
#: files that differ only in the discipline they impose — which is a far more
#: honest demonstration than a branch in the function below.
RUBRIC = prompts.render("rubric")
GRADER_PROMPT = prompts.render("rubric_grader")


@dataclass
class AgentBundle:
    """A built agent plus everything needed to inspect or verify its run."""

    agent: Any
    workspace: RunWorkspace
    mode: str
    tool_names: dict[str, list[str]]
    sources: list[ServerTools] = field(default_factory=list)
    meter: RunMeter = field(default_factory=RunMeter)

    @property
    def verified(self) -> bool:
        return self.mode == "verified"

    @property
    def degraded(self) -> list[ServerTools]:
        """Servers asked for live data that are answering with fixtures."""
        return [s for s in self.sources if s.fallback_reason]


def _seed_workspace(ws: RunWorkspace) -> None:
    """Copy skills and memory into the run directory.

    The backend is rooted at the run directory, so skills have to live inside
    it. Copying rather than symlinking keeps each run's instructions frozen at
    the version it ran with — otherwise editing a skill silently changes what a
    finished run "was", and reproducing a result becomes impossible.
    """
    src_skills = REPO_ROOT / "skills"
    if src_skills.exists():
        shutil.copytree(src_skills, ws.root / "skills", dirs_exist_ok=True)
    src_memory = REPO_ROOT / "AGENTS.md"
    if src_memory.exists():
        shutil.copy2(src_memory, ws.root / "AGENTS.md")


def _permissions() -> list[FilesystemPermission]:
    """Make recorded evidence append-only.

    Rules are first-match-wins, so the denies come first. Sources and the
    ledger are written by tools and middleware, which go through the backend
    directly and are unaffected; what is blocked is the *model* editing them
    with `write_file` or `edit_file`. An agent that can edit its own evidence
    can make any claim verify, which would make the entire verification story
    theatre.
    """
    return [
        FilesystemPermission(
            operations=["write"], paths=[f"/{SOURCES_DIR}/**"], mode="deny"
        ),
        FilesystemPermission(operations=["write"], paths=[f"/{LEDGER_FILE}"], mode="deny"),
        FilesystemPermission(operations=["read", "write"], paths=["/**"], mode="allow"),
    ]


async def build_wealth_agent(
    *,
    mode: str = "baseline",
    workspace: RunWorkspace | None = None,
    settings: Settings | None = None,
    policy: Policy | None = None,
    model: Any = None,
    weak_researcher: bool = False,
    grader_model: Any = None,
    on_rubric_evaluation: Any = None,
    meter: RunMeter | None = None,
    always_judge: bool = False,
) -> AgentBundle:
    """Build the supervisor and its subagents.

    Args:
        mode: One of :data:`MODES`.

            - ``naive`` — no skills, no grounding rules, no verification. What a
              competent team ships before anyone asks how they know it is right.
            - ``baseline`` — skills and grounding rules, but no verifier. The
              agent is *told* the discipline; nothing checks it.
            - ``verified`` — adds the verifier subagent and the
              ``RubricMiddleware`` runtime grading loop.

            All three record to the same ledger, so all three are measurable —
            including the one that was not built to be.
        workspace: Reuse an existing run directory instead of creating one.
        settings: Overrides process-wide settings (demo vs live, write tools).
        model: Model for the supervisor and analysts.
        weak_researcher: Run the market researcher on a smaller model.
        grader_model: Model for the `RubricMiddleware` grader.
        on_rubric_evaluation: Callback fired after each grading iteration.
            Useful for showing `needs_revision → satisfied` live.

    Returns:
        The compiled agent, its workspace, and the tool names that were wired
        up — so a caller can print exactly what the model can see.
    """
    if mode not in MODES:
        msg = f"mode must be one of {MODES}, got {mode!r}"
        raise ValueError(msg)
    verified = mode == "verified"
    settings = settings or SETTINGS
    meter = meter if meter is not None else RunMeter()
    policy = policy or load_policy()

    # Constructed here rather than passed as strings, because a string cannot
    # carry the caching configuration and caching is the largest single lever
    # on what this run costs. See `models.py`.
    model = model if model is not None else build_model(REASONING_MODEL)
    grader_model = grader_model if grader_model is not None else build_model(WORKING_MODEL)
    #: The analysts call four deterministic tools and write a paragraph, and the
    #: verifier reads a verdict off one. Neither improves with a frontier model,
    #: because the numbers come from Python either way.
    analyst_model = build_model(WORKING_MODEL)

    #: The strategist is the exception, and it is worth being explicit about why.
    #: Its arithmetic comes from `rebalance_plan` like everything else — but it
    #: is the only agent making a *judgement* a human then acts on with money:
    #: which drifts matter, which to leave alone, how to explain the trade-off
    #: when two policy rules disagree. Tiering is for jobs where a smaller model
    #: reaches the same answer. This is not one of them, and saving two cents on
    #: the recommendation is a bad trade at any volume.
    strategist_model = model
    ws = workspace or RunWorkspace()
    _seed_workspace(ws)

    client = build_client(settings)
    # Per server, not per run: the two Robinhood servers have different
    # admission policies, so "am I on live data?" has two answers.
    sources = [
        await load_server_tools(TRADING, settings=settings, client=client),
        await load_server_tools(BANKING, settings=settings, client=client),
    ]
    trading, banking = (s.split for s in sources)

    by_name = {t.name: t for t in [*trading.all, *banking.all]}
    # Fixture names pass straight through; live ones route via the schema
    # adapter. The analytics layer below never learns which it got.
    capabilities = resolve_capability_tools(by_name)
    portfolio_tools = build_portfolio_tools(
        ws, capabilities["positions"], capabilities["balances"]
    )
    spend_tools = build_spend_tools(ws, capabilities["card_transactions"])
    allocation_tools = build_allocation_tools(ws, policy)
    research_tools = build_research_tools(ws)
    verification_tools = build_verification_tools(ws)

    subagents: list[Any] = build_analyst_subagents(
        portfolio_tools=portfolio_tools,
        spend_tools=spend_tools,
        research_tools=research_tools,
        model=model,
        # Each subagent needs its own recording middleware — declarative
        # subagents are compiled with their own stack and do not inherit the
        # `middleware` passed below. See build_analyst_subagents' docstring.
        ledger=ws.ledger,
        analyst_model=analyst_model,
        researcher_model=build_model(WEAK_RESEARCHER_MODEL) if weak_researcher else None,
        meter=meter,
    )
    # The strategist runs after both analysts, so it is a subagent the
    # supervisor calls rather than one that fans out in parallel with them.
    subagents.append(
        build_allocation_strategist(allocation_tools, strategist_model, ws.ledger, meter)
    )

    middleware: list[AgentMiddleware] = [
        TodoListMiddleware(),
        retry_middleware(),
        call_limit("supervisor"),
        GroundingLedgerMiddleware(ws.ledger, agent_name="supervisor"),
        CostMeterMiddleware(meter, agent_name="supervisor"),
    ]

    prompt = prompts.render("supervisor_naive" if mode == "naive" else "supervisor")
    if verified:
        # The free check runs first and short-circuits. On a clean memo the
        # verifier subagent and the rubric grader below never execute, which is
        # this module's own stated principle applied to its own control flow.
        middleware.append(VerificationGateMiddleware(ws))
        subagents.append(build_verifier_subagent(verification_tools, analyst_model, meter))
        prompt += "\n" + prompts.render("supervisor_verified_suffix")
        if always_judge:
            # The LLM grading loop, on demand rather than by default.
            #
            # It used to run on every verified run, alongside a verifier
            # subagent the prompt delegated to unconditionally — three
            # verification systems for one memo. They thrashed: the gate asked
            # for a revision, the rubric asked for a different one, and the
            # verifier was re-invoked after each, which is how a "cost saving"
            # change made a run 40% more expensive than the version it replaced.
            #
            # The deterministic gate above answers the same question for free
            # and answers it better, because it names lines. This stays because
            # the workshop teaches the rubric pattern and because a judge does
            # catch things code cannot — but it is opt-in, which is what the
            # rest of this repo argues for.
            middleware.append(
                RubricMiddleware(
                    model=grader_model,
                    # The built-in grader prompt does not state the one
                    # invariant its own response schema enforces — `satisfied`
                    # requires every criterion to have passed. A smaller grader
                    # violates it often enough to matter: the structured-output
                    # validator rejects the response, the iteration is
                    # discarded, and the run burns a turn producing nothing.
                    system_prompt=GRADER_PROMPT,
                    # The grader gets the deterministic checker as a tool rather
                    # than being asked to eyeball groundedness. That turns "does
                    # this look right?" into "what does the check say?", a far
                    # easier question with a far more stable answer.
                    tools=verification_tools,
                    max_iterations=2,
                    on_evaluation=on_rubric_evaluation,
                )
            )

    # Write tools stay behind a human gate whether or not they are visible.
    # `ALLOW_WRITE_TOOLS` controls visibility; this controls execution. Two
    # switches, because "the model can see it" and "it happens without me" are
    # different risks.
    write_tool_names = [t.name for t in (trading.write + banking.write)]
    interrupt_on = {name: True for name in write_tool_names}

    agent = create_deep_agent(
        model=model,
        system_prompt=prompt,
        subagents=subagents,
        middleware=middleware,
        backend=FilesystemBackend(root_dir=str(ws.root), virtual_mode=True),
        # The naive agent gets no skills — that is the whole difference. Skills
        # are where the memo-format and verification-protocol discipline lives,
        # so removing them is what turns a careful agent back into a normal one.
        skills=None if mode == "naive" else ["/skills/"],
        memory=["/AGENTS.md"],
        permissions=_permissions(),
        interrupt_on=interrupt_on or None,
        checkpointer=InMemorySaver(),
        name="wealth-supervisor",
    )

    return AgentBundle(
        agent=agent,
        workspace=ws,
        mode=mode,
        tool_names={
            "portfolio": [t.name for t in portfolio_tools],
            "spend": [t.name for t in spend_tools],
            "research": [t.name for t in research_tools],
            "allocation": [t.name for t in allocation_tools],
            "verification": [t.name for t in verification_tools],
            "mcp_read": trading.names()["read"] + banking.names()["read"],
            "mcp_write_gated": write_tool_names,
        },
        sources=sources,
        meter=meter,
    )


__all__ = [
    "GRADER_PROMPT",
    "RUBRIC",
    "AgentBundle",
    "build_wealth_agent",
]
