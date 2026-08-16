"""The four subagents, and why one of them is deliberately not a deep agent.

A subagent is a context window with a job. The harness you give it should
follow from the job, not from habit:

===================  ======  ==================================================
subagent             deep?   why
===================  ======  ==================================================
portfolio-analyst    yes     Open-ended. Decides what to look at next based on
                             what it just found, and offloads position data to
                             the filesystem so the supervisor never sees it.
spend-analyst        yes     Same shape, over six months of transactions. Reads
                             the category rulebook skill on demand.
market-researcher    yes     The most open-ended of the three: search, read,
                             follow a lead, read again. Needs its own context to
                             burn and its own scratch space.
verifier             **no**  Bounded. Runs two deterministic checks and reports.
                             There is exactly one right answer, no planning to
                             do, and nothing to write down. A deep-agent harness
                             here would add a filesystem it will not use, a
                             `task` tool it must not use, and latency.
===================  ======  ==================================================

The verifier is the interesting one. It is a plain `create_agent` ReAct loop
wrapped as a `CompiledSubAgent`, and making that choice explicitly — rather
than reaching for `create_deep_agent` a fourth time — is most of what
"architecture" means here. The reflex to give every component the most capable
harness available is how agent systems get slow and unpredictable.
"""

from __future__ import annotations

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelRetryMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from deepagents import CompiledSubAgent, SubAgent

from wealth_agent.ledger_middleware import GroundingLedgerMiddleware
from wealth_agent.store import PORTFOLIO_DIR, SOURCES_DIR, SPEND_DIR, GroundingLedger


def _subagent_middleware(ledger: GroundingLedger, *, name: str) -> list[AgentMiddleware]:
    """The middleware every deep subagent needs.

    Both entries are cross-cutting concerns, and both have to be installed
    *per subagent* — declarative subagents are compiled with their own stack and
    inherit nothing from the parent. Centralizing them here rather than
    repeating the list three times means adding a fourth subagent cannot
    accidentally opt out of either.
    """
    from wealth_agent.supervisor import transient_errors

    return [
        ModelRetryMiddleware(
            max_retries=4,
            initial_delay=2.0,
            max_delay=30.0,
            retry_on=transient_errors(),
            on_failure="error",
        ),
        GroundingLedgerMiddleware(ledger, agent_name=name),
    ]

PORTFOLIO_ANALYST = "portfolio-analyst"
SPEND_ANALYST = "spend-analyst"
MARKET_RESEARCHER = "market-researcher"
VERIFIER = "verifier"


PORTFOLIO_PROMPT = f"""\
You are the portfolio analyst. You answer questions about what this person owns.

Your workflow:
1. Call `load_portfolio` first. It caches positions and balances to \
/{PORTFOLIO_DIR}/ and returns a summary.
2. Use `concentration`, `sector_exposure`, and `unrealized_pl_summary` for \
derived figures. Never compute a percentage or a total yourself — call the tool \
that computes it. The tools exist precisely so that every number in the final \
memo can be traced back to a computation.
3. Use `position_detail` for a single holding.

Rules:
- Report only figures a tool returned. If you need a number no tool provides, \
say so rather than estimating it.
- When you report a percentage, state its denominator. `concentration` is \
against total value including cash; `sector_exposure` is against equity only. \
Conflating them is the most common error in this kind of analysis.
- Return a compact written summary, not raw JSON dumps. The supervisor has a \
limited context window and cannot use a wall of numbers.
"""


SPEND_PROMPT = f"""\
You are the spend analyst. You answer questions about where money went.

Your workflow:
1. Call `load_spend_data` first. It pulls the card feed, normalizes messy \
descriptors into merchants and categories, and caches to /{SPEND_DIR}/. It \
returns counts only — the rows stay on disk on purpose.
2. Use `spending_by_category`, `spending_by_merchant`, `monthly_trend`, \
`find_recurring_charges`, `largest_transactions`, and `summarize_period`.

Rules:
- Report only figures a tool returned. Do not add up categories yourself.
- Refunds and statement payments are excluded from spend totals by the tools. \
If someone asks about total outflow including payments, say that the tools \
measure charges only.
- `monthly_trend` flags the final month as `partial` when the data ends \
mid-month. Never compare a partial month to full months without saying so — \
that is how a reporting artifact becomes a fake trend.
- If `load_spend_data` reports uncategorized charges, mention how many. An \
unexplained gap in the taxonomy is worth surfacing, not hiding.
- Return a compact written summary.
"""


RESEARCH_PROMPT = f"""\
You are the market researcher. You find external evidence and make it citable.

Your workflow:
1. `web_search` to find candidate pages.
2. `fetch_page` on the ones worth reading. This stores the full text under a \
source id and returns a preview.
3. `read_source` if you need more of a page than the preview showed.
4. `list_sources` to see what you have gathered.

Rules:
- A search snippet is NOT a source. It is the search engine's summary. You may \
only cite a page you called `fetch_page` on.
- Every claim you report back must carry its source id, written as \
[src_xxxxxxxx]. A claim without one cannot survive verification and will be \
stripped from the memo.
- When you quote, quote exactly. Reproduce the span character for character \
from the page. Paraphrase is fine, but then do not put it in quotation marks — \
a quoted span is checked literally against the stored text in /{SOURCES_DIR}/.
- Prefer primary sources: company filings and press releases over aggregator \
articles about them.
- Return a compact summary with the source id on each claim.
"""


VERIFIER_PROMPT = """\
You verify a memo against the evidence actually recorded during this run.

Call `verify_report` with the full memo text. It runs deterministic checks and \
returns per-claim verdicts. Optionally call `evidence_summary` first to see \
what evidence exists.

Report back:
1. The grounding score and whether it passed.
2. Every `fabricated` finding, quoted, with the line number. These are the \
serious ones: a citation to a source that was never fetched, or a quoted span \
that is not in the source it is attributed to.
3. Every `unsupported` finding, grouped. These are figures that appear in no \
tool result and no fetched page. They may still be true; we simply have no \
record supporting them.
4. For each failure, the specific fix: which claim to remove, re-attribute, or \
replace with a figure a tool actually returned.

Do not soften the findings and do not speculate about whether an unsupported \
number is probably right. Your job is to report what the evidence shows.
"""


def build_analyst_subagents(
    *,
    portfolio_tools: list[BaseTool],
    spend_tools: list[BaseTool],
    research_tools: list[BaseTool],
    model: str | BaseChatModel,
    ledger: GroundingLedger,
    researcher_model: str | BaseChatModel | None = None,
) -> list[SubAgent]:
    """The three deep subagents.

    Args:
        portfolio_tools: Built by ``build_portfolio_tools``.
        spend_tools: Built by ``build_spend_tools``.
        research_tools: Built by ``build_research_tools``.
        model: Model for the analysts.
        ledger: The run's grounding ledger. **Each subagent gets its own
            recording middleware** — see the note below.
        researcher_model: Optional override for the researcher. Used by the
            workshop's "weak researcher" configuration, which reproduces
            citation drift on demand — a cheaper model compressing six sources
            re-attributes claims far more often, which is exactly the failure
            the verifier exists to catch.

    Note:
        Declarative subagents do **not** inherit the parent agent's
        ``middleware`` list. Each one is compiled with its own stack, so a
        recording middleware installed only on the supervisor sees only the
        supervisor's tool calls.

        We shipped that bug and it was invisible in the nicest possible way:
        the agent ran fine, the memo was good, and the verifier reported
        ``$18,420.55`` — the actual cash balance, straight from
        ``get_account_balances`` — as unsupported. Nothing errored. The
        evidence had simply been destroyed at the boundary between two context
        windows, which is the precise failure this whole system exists to
        catch. It caught it. On itself.

        The lesson generalizes past this bug: **in a multi-agent system,
        cross-cutting concerns have to be installed per agent.** Logging,
        redaction, rate limiting, and audit trails all have this shape, and
        all of them fail silently the same way.
    """
    return [
        SubAgent(
            name=PORTFOLIO_ANALYST,
            description=(
                "Analyzes holdings: positions, balances, concentration, sector "
                "exposure, and unrealized P/L. Delegate anything about what the "
                "person owns or how their portfolio is allocated."
            ),
            system_prompt=PORTFOLIO_PROMPT,
            tools=portfolio_tools,
            model=model,
            middleware=_subagent_middleware(ledger, name=PORTFOLIO_ANALYST),
        ),
        SubAgent(
            name=SPEND_ANALYST,
            description=(
                "Analyzes card spending: totals by category and merchant, "
                "monthly trends, recurring subscriptions, and period "
                "comparisons. Delegate anything about where money went."
            ),
            system_prompt=SPEND_PROMPT,
            tools=spend_tools,
            model=model,
            middleware=_subagent_middleware(ledger, name=SPEND_ANALYST),
        ),
        SubAgent(
            name=MARKET_RESEARCHER,
            description=(
                "Researches the web for external context on holdings, sectors, "
                "or markets, and stores every page it reads as a citable "
                "source. Delegate anything requiring outside information."
            ),
            system_prompt=RESEARCH_PROMPT,
            tools=research_tools,
            model=researcher_model or model,
            middleware=_subagent_middleware(ledger, name=MARKET_RESEARCHER),
        ),
    ]


def build_verifier_subagent(
    verification_tools: list[BaseTool], model: str | BaseChatModel
) -> CompiledSubAgent:
    """The verifier — a plain ReAct agent, not a deep agent.

    Compiled here rather than declared as a ``SubAgent`` so it gets exactly two
    tools and nothing else: no filesystem, no `task` tool, no planning, no
    summarization. Its job has one right answer and it should reach it in two
    tool calls.
    """
    runnable = create_agent(
        model=model,
        tools=verification_tools,
        system_prompt=VERIFIER_PROMPT,
        name=VERIFIER,
    )
    return CompiledSubAgent(
        name=VERIFIER,
        description=(
            "Checks a finished memo against the evidence recorded this run and "
            "reports every citation or figure that does not hold up. Call this "
            "before returning any memo to the user."
        ),
        runnable=runnable,
    )


__all__ = [
    "MARKET_RESEARCHER",
    "PORTFOLIO_ANALYST",
    "SPEND_ANALYST",
    "VERIFIER",
    "build_analyst_subagents",
    "build_verifier_subagent",
]
