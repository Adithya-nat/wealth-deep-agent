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
from dataclasses import dataclass
from typing import Any

from deepagents import (
    FilesystemPermission,
    RubricMiddleware,
    create_deep_agent,
)
from deepagents.backends import FilesystemBackend
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRetryMiddleware,
    TodoListMiddleware,
)
from langgraph.checkpoint.memory import InMemorySaver

from wealth_agent.config import REPO_ROOT, SETTINGS, Settings
from wealth_agent.ledger_middleware import GroundingLedgerMiddleware
from wealth_agent.mcp_clients import BANKING, TRADING, build_client, load_tools
from wealth_agent.store import LEDGER_FILE, SOURCES_DIR, RunWorkspace
from wealth_agent.subagents import (
    build_analyst_subagents,
    build_verifier_subagent,
)
from wealth_agent.tools import (
    build_portfolio_tools,
    build_research_tools,
    build_spend_tools,
)
from wealth_agent.tools.verification import build_verification_tools

DEFAULT_MODEL = "anthropic:claude-sonnet-4-6"


def transient_errors() -> tuple[type[Exception], ...]:
    """Exception types worth retrying, and only those.

    Retrying on bare `Exception` is a trap this repo fell into. A run hit
    `BadRequestError: your credit balance is too low`, the middleware retried it
    five times, gave up, and — with the default `on_failure="continue"` —
    handed the error *string* back to the agent as if it were a model response.
    The agent dutifully wrote it into the memo. Twelve experiment runs reported
    `success`, every score came back 0.0, and nothing anywhere said "you are out
    of credits."

    A billing error, a bad API key, and a malformed request are all permanent.
    Retrying them wastes time and, worse, converts a loud failure into a quiet
    wrong answer. Connection drops and rate limits are the retryable ones.
    """
    try:
        from anthropic import (
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
            RateLimitError,
        )
    except ImportError:  # pragma: no cover
        return (ConnectionError, TimeoutError)
    return (
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        RateLimitError,
        ConnectionError,
        TimeoutError,
    )

#: A deliberately weaker model for the researcher, used by the workshop to make
#: citation drift reproducible. Compressing six sources into one summary is
#: where attribution gets lost, and a smaller model loses it every run instead
#: of one run in twelve. Said out loud during the demo: the honest version of
#: "here is a bug" is "here is a bug I can reproduce".
WEAK_RESEARCHER_MODEL = "anthropic:claude-haiku-4-5"

#: The grader for the runtime rubric loop. Smaller than the working model on
#: purpose — grading against a deterministic tool's output is a much easier job
#: than producing the analysis, and paying Sonnet prices to read a verdict is
#: waste.
DEFAULT_GRADER_MODEL = "anthropic:claude-haiku-4-5"

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

NAIVE_PROMPT = """\
You are a wealth analyst. Produce a clear, well-written memo about someone's
finances, covering their portfolio allocation, their spending, and any relevant
market context.

You do not gather data yourself. You delegate:
- `portfolio-analyst` — holdings, allocation, concentration, unrealized P/L
- `spend-analyst` — card spending, categories, merchants, subscriptions, trends
- `market-researcher` — external context

Plan with `write_todos`, delegate in parallel where tasks are independent, then
synthesize the findings into a memo. Write it to /memo.md and return it as your
final message. Make it readable and useful.
"""

SUPERVISOR_PROMPT = """\
You are a wealth analyst. You produce a written memo about someone's finances \
that a human will act on, so every claim in it has to be defensible.

You do not gather data yourself. You delegate:
- `portfolio-analyst` — holdings, allocation, concentration, unrealized P/L
- `spend-analyst` — card spending, categories, merchants, subscriptions, trends
- `market-researcher` — external context, with citable sources

Plan the work with `write_todos` before delegating. Delegate in parallel when \
tasks are independent — portfolio and spend analysis never depend on each other.

Read `/skills/memo-format/SKILL.md` before writing the memo. It defines the \
citation and figure rules the memo is checked against.

Absolute rules:
- Every figure in the memo must come from a tool result reported by a subagent. \
Never compute, estimate, round, or infer a number yourself. If you want a \
figure no tool produced, delegate for it or leave it out.
- Every external claim must carry its source id as [src_xxxxxxxx].
- State the denominator whenever you report a percentage.
- If the evidence does not support a conclusion, say what is missing. A memo \
that says "we could not verify this" is worth more than one that guesses.

Write the finished memo to /memo.md and also return it as your final message.
"""

VERIFIED_SUFFIX = """\

Before you finish, you MUST delegate the memo to `verifier`. Send it the full \
memo text. If it reports any `fabricated` finding, or a grounding score below \
0.95, fix the memo and verify again. Do not return a memo that has not passed.
"""

RUBRIC = """\
- Every figure in the memo traces to a recorded tool result (verify_report \
reports zero `unsupported` figures).
- Every citation resolves to a source that was actually fetched, and every \
quoted span appears in the source it is attributed to (zero `fabricated` \
findings).
- The overall grounding score from verify_report is at least 0.95.
- Every percentage states its denominator.
- The memo covers portfolio allocation, spending, and at least one externally \
sourced piece of market context.
"""


@dataclass
class AgentBundle:
    """A built agent plus everything needed to inspect or verify its run."""

    agent: Any
    workspace: RunWorkspace
    mode: str
    tool_names: dict[str, list[str]]

    @property
    def verified(self) -> bool:
        return self.mode == "verified"


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
    model: str = DEFAULT_MODEL,
    weak_researcher: bool = False,
    grader_model: str = DEFAULT_GRADER_MODEL,
    on_rubric_evaluation: Any = None,
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
    ws = workspace or RunWorkspace()
    _seed_workspace(ws)

    client = build_client(settings)
    trading = await load_tools(TRADING, settings=settings, client=client)
    banking = await load_tools(BANKING, settings=settings, client=client)

    by_name = {t.name: t for t in [*trading.all, *banking.all]}
    portfolio_tools = build_portfolio_tools(
        ws, by_name["get_positions"], by_name["get_account_balances"]
    )
    spend_tools = build_spend_tools(ws, by_name["get_card_transactions"])
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
        researcher_model=WEAK_RESEARCHER_MODEL if weak_researcher else None,
    )

    middleware: list[AgentMiddleware] = [
        TodoListMiddleware(),
        # A long run makes hundreds of model calls across four agents. Over
        # seventeen minutes, the probability that *none* of them hits a
        # transient connection error is not close to one — and without a retry,
        # a single dropped socket at minute fourteen discards the whole run and
        # everything it cost.
        #
        # `retry_on` is scoped and `on_failure="error"` is explicit. See
        # transient_errors() for why: the defaults turn a permanent failure into
        # a silent wrong answer, which is worse than the crash they prevent.
        ModelRetryMiddleware(
            max_retries=4,
            initial_delay=2.0,
            max_delay=30.0,
            retry_on=transient_errors(),
            on_failure="error",
        ),
        GroundingLedgerMiddleware(ws.ledger, agent_name="supervisor"),
    ]

    prompt = NAIVE_PROMPT if mode == "naive" else SUPERVISOR_PROMPT
    if verified:
        subagents.append(build_verifier_subagent(verification_tools, model))
        prompt += VERIFIED_SUFFIX
        middleware.append(
            RubricMiddleware(
                model=grader_model,
                # The grader gets the deterministic checker as a tool rather
                # than being asked to eyeball groundedness. Giving a judge the
                # ability to gather evidence turns "does this look right?" into
                # "what does the check say?", which is a far easier question and
                # a far more stable answer.
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
            "verification": [t.name for t in verification_tools],
            "mcp_read": trading.names()["read"] + banking.names()["read"],
            "mcp_write_gated": write_tool_names,
        },
    )


__all__ = [
    "DEFAULT_GRADER_MODEL",
    "DEFAULT_MODEL",
    "RUBRIC",
    "WEAK_RESEARCHER_MODEL",
    "AgentBundle",
    "build_wealth_agent",
]
