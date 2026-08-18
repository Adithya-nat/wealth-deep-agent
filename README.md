# Wealth Agent

**An agent that reads your brokerage account, your card spending, and your
investment policy — then tells you what to move, and shows you where every
number came from.**

---

## What this is

A *wealth memo* is the document a financial advisor writes before a client
review. It says: here is what you own, here is where your money went, here is
what I think you should change, and here is the evidence for each claim.
Producing one is several hours of work, and most of those hours are mechanical.

This is an agent that produces one.

### What goes in

| Input | Where it comes from |
|---|---|
| **Positions and balances** — what you own, what you paid | Robinhood Trading MCP server |
| **Six months of card transactions** — where the money went | Robinhood Banking MCP server |
| **An investment policy** — what the portfolio is *supposed* to look like | [`policies/balanced-growth.json`](policies/balanced-growth.json), checked in and editable |
| **Current market context** — what has changed about your holdings | Live web search, with every page stored |

### What comes out

An HTML report with specific, dollar-denominated recommendations — not "consider
reducing tech exposure", but:

```
TRIM   AAPL   −$11,759.48   Information Technology is 34.71% of equity
                            against a 25% target with a 5-point band

TRIM   VOO    −$18,651.05   VOO is 23.36% of total portfolio value
                            against a 10% single-name cap
                            ⚠ conflicts with the sector target — flagged, not resolved

BUY    BND     +$9,690.92   Fixed Income is 0% of equity against an 8% target
                            funded from the trims, because cash is already
                            $3,158.65 below its four-month reserve floor
```

Every one of those figures was computed by Python, not by a model. Every claim
about the market carries the id of a page that was actually fetched. And in the
report, **every number is clickable** — click it and you see which tool produced
it and which agent was holding it at the time.

Try it without running anything:

```bash
make report        # pick a frozen run, opens in your browser
```

---

## Why an agent, and why this one

A human doing this by hand spends hours per review, and most of it is not
judgment: reconciling positions, categorizing transactions, computing weights
against policy, deriving trade sizes, chasing sources, formatting. The agent
does it in about five minutes for about a dollar.

**But that is only half the value, and the second half is the one every demo
forgets.** A wrong figure in a client-facing financial memo is not a rough edge;
it is a compliance finding. So if a human has to re-derive 92 figures to trust
the output, nothing has actually been saved — you have moved the work, not
removed it.

That is what the rest of this repo is about. Every tool result is recorded as it
arrives, and every claim in the finished memo is checked against that record
before a person sees it. On the last run:

> **142 claims checked automatically. 2 needed a human.**

Reviewing two flagged claims is a different job from re-deriving a hundred and
forty-two. **The agent writing the memo saves hours; the agent making the memo
checkable is what lets you actually use them.**

---

## Getting started

```bash
make            # the list of everything you can do
make setup      # install, then check your keys
make menu       # not sure? start here — it asks what you want
```

Nothing above needs a flag. Everything that needs input asks for it.

`make demo` walks the whole workshop, one keypress per beat, entirely offline.

---

# Trust, but Verify

The rest of this document is the workshop the agent above was built to teach:
how you know an agent's output is right, and how you know it still is next week
after someone changes a prompt.

Workshop material for a 45-minute session. See **[WORKSHOP_BRIEF.md](WORKSHOP_BRIEF.md)**
for the topic, audience, objectives, and where it sits in a curriculum.

Everything below runs **offline against fixtures**. No API keys, no accounts,
no network, no real financial data.

---

## Setup

```bash
make setup       # uv sync, then a pre-flight check
make test        # 189 tests, no keys, no network
```

To run the agent itself you need one key:

```bash
cp .env.example .env     # then set ANTHROPIC_API_KEY
```

---

## The measured result

Three configurations. Same subagents, same tools, same data, same question.

```bash
make compare     # free, offline, reproducible with the network unplugged
```

| configuration | grounding | citations | fabricated | ships? |
|---|---|---|---|---|
| **naive** — no skills, no rules, no verification | 92.4% | **0** | 0 | ✗ |
| **baseline** — skills + grounding rules, nothing checks them | 99.2% | 32 | 0 | ✓ |
| **verified** — + verifier subagent + runtime rubric loop | 98.6% | 42 | 0 | ✓ |

Sit with the naive memo. It made ~20 claims about Apple's China antitrust
exposure, EU DMA fines, and tariff guidance and attributed **none** of them. It
rounded six real tool outputs into tidier numbers. And it is comfortably the
**best-written** of the three — the only one that closes with a prioritized
action plan. The most persuasive memo was the least defensible, and reading it
carefully would not have told you.

All four runs are frozen in `artifacts/runs/`, so every number here is
reproducible offline.

## The demo

```bash
make demo        # the whole session, one keypress per beat, offline
```

Or reach for the pieces directly:

| Command | What it shows |
|---|---|
| `make report` | Any run's report, rendered from recorded data. No model calls. |
| `make compare` | The table above, recomputed from the frozen runs. |
| `make judge` | Loop 0 — the deterministic checker against 20 human labels. |
| `make cost` | What the last run cost, and how much was served from cache. |
| `make doctor` | Keys, prompts, policy, fixtures. Run before you present. |

`FACILITATOR.md` has the timed script with talking points.

---

## Loop 0: the judge is an application too

Measured over the 20 labeled fixtures, same judge model throughout:

| judge prompt | agreement | blessed a bad memo | flagged a good memo |
|---|---|---|---|
| v1, a reasonable first draft | 85% | **2** | 1 |
| v2, strict — written after reading v1's mistakes | 85% | **0** | 3 |
| v3, calibrated | **95%** | **0** | 1 |

Look at v2. By the headline number it was a complete waste of time — 85% before,
85% after. It had in fact eliminated the entire dangerous error class and
introduced a milder one. **A single agreement number would have told you to
throw away the change that mattered most.**

---

## Two more bugs, same shape

**A cost ceiling that truncated a run in silence.** I capped the supervisor at
30 model calls because a healthy run used about 30. A run hit exactly 30, was
stopped mid-sentence, and reported a grounding score and a report path as though
it had finished. A ceiling that binds during normal operation is set wrong;
these are now ~3x observed usage, and hitting one is reported as a defect above
everything else rather than absorbed as a slightly shorter memo.

**A verification loop that detected and never acted.** The deterministic gate
ran in an `after_agent` hook and returned the findings as a message, expecting
the agent to revise. `after_agent` runs when the agent has already finished, so
returning messages updated state and restarted nothing. On one run the gate
caught two fabricated quotes, the panel logged *"98.1% grounded — 2 to fix,
revising"*, and the run ended with both still in the memo.

Detection worked perfectly and the fix never fired, which is the most dangerous
shape a safety control can take: **the logs said it acted.** The loop now lives
in the caller, which can actually invoke the agent again, and a test asserts the
middleware no longer overrides `after_agent`.

**A checker that could not read a minus sign.** Three claims were flagged as
unsupported on a run — all of them the same true figure. The memo wrote
`−$1,621.90` with U+2212, the extractor only matched an ASCII hyphen, so it read
`+1621.90` and found nothing to match. The fix is narrow on purpose: a dash is
only a sign when it is *attached* to the number, because normalizing every dash
would turn `revenue — $94.04B` negative. The verifier is an application too, and
it needed its own tests.

## The bug this repo caught in itself

`GroundingLedgerMiddleware` was installed on the supervisor only. Declarative
subagents don't inherit parent middleware — they're compiled with their own
stack — so every tool call made *inside* a subagent never reached the ledger.

Nothing errored. The agent ran beautifully. The verifier reported `$18,420.55`
— the actual cash balance, straight out of `get_account_balances` — as
unsupported, because the evidence had been destroyed at the boundary between two
context windows. Which is the exact thesis of this workshop.

One-line fix: **76.58% → 99.24%**. The before-run is frozen as
`artifacts/runs/ledger-bug`, and `tests/test_ledger_middleware.py` asserts on
the configuration so it cannot silently return.

The lesson generalizes past the bug: **in a multi-agent system, cross-cutting
concerns have to be installed per agent.** Logging, redaction, rate limiting,
and audit trails all have this shape, and all of them fail silently the same way.

---

## Architecture

One file per agent, so the directory listing *is* the architecture:

```
src/wealth_agent/agents/
  supervisor.py              create_deep_agent · plans, delegates, writes the memo
  │   TodoListMiddleware · GroundingLedger · CostMeter · ModelCallLimit · Rubric
  │   skills · memory · filesystem permissions · interrupt_on(writes)
  │
  ├── portfolio_analyst.py       DEEP    Robinhood Trading MCP → /portfolio/
  ├── spend_analyst.py           DEEP    Robinhood Banking MCP → /spend/
  ├── market_researcher.py       DEEP    web search + fetch    → /sources/
  ├── allocation_strategist.py   NOT DEEP  policy → drift → typed recommendations
  └── verifier.py                NOT DEEP  two deterministic checks, nothing else
```

**A subagent is a context window with a job.** Give it a deep-agent harness only
when the job is open-ended. The verifier's job has exactly one right answer, so
it is a plain `create_agent` ReAct loop — no filesystem it would not use, no
`task` tool it must not use, no planning, no latency. Reaching for
`create_deep_agent` a fourth time would have been the easy call and the wrong
one.

The **allocation strategist** is the second instance of the same call, which is
what makes it a principle rather than an anecdote. Its job is bounded — call four
deterministic tools, decide which drifts matter, return a validated
`RecommendationSet` — so it is a plain ReAct loop with `response_format` and
nothing else. See [`src/wealth_agent/agents/`](src/wealth_agent/agents/).

### The idea the rest hangs on

> **You cannot evaluate what you did not record.**

An agent that reads a balance, compresses it through a subagent, and writes a
memo has destroyed the link between the number on the page and the API response
it came from. So [`GroundingLedgerMiddleware`](src/wealth_agent/middleware/grounding_ledger.py)
records every tool result as it arrives — *middleware*, not per-tool code, so
no future tool can forget — and [`verify.py`](src/wealth_agent/verify.py) checks
the memo against it afterwards. Two checks, no LLM:

- **Citation grounding** — every cited source was actually fetched, and every
  quoted span really appears in it.
- **Numeric grounding** — every figure traces to a tool result or a fetched
  page. No model-invented numbers.

Offloading a fetched page to the filesystem for *context* and offloading it for
*verifiability* turn out to be the same move. That is the nicest thing about
this design.

---

## Two kinds of check, and why only one of them is a gate

Grounding is an **invariant**: a citation either resolves or it does not, and
that gets asserted. Prose style is a **distribution**: a memo with two clumsy
phrases is not broken. Both get checked deterministically — `verify.py` for
claims, `voice.py` for phrasing — and only the first can fail a run.

The voice rules are the [Humanizer skill](https://github.com/blader/humanizer)
(MIT, v2.11.1, based on Wikipedia's *Signs of AI writing*), vendored verbatim at
`skills/humanizer/` — 456 lines and 35 patterns with worked examples.
`skills/memo-voice/` is a 70-line adapter carrying only what changes because
this is a financial memo.

Both are deep-agent skills, which is the point: their frontmatter costs a line
of context per turn, and the bodies load only when the agent opens them while
writing. Paraphrasing someone else's 456-line skill into a prompt would have
cost those tokens on all forty model calls and gone stale the first time
upstream changed.

`voice.py` then checks the result and reports it beside the grounding numbers
without scoring it. Only part of the skill is mechanizable — "forced groups of
three" needs judgement, so it stays in the skill and out of the lint.

One carve-out is written into the skill in capitals, because it is the rule that
matters: **never drop a figure, a source id, or a caveat to improve a sentence.**
"No tool in this run returns tax lots, so the tax cost is not estimated" reads
like a hedge and is a fact. Prose advice that quietly removes uncertainty from a
financial memo has made it worse, not better.

Teams get this backwards constantly — asserting on the distribution, watching it
flake, and concluding agents are untestable. Same lesson as "why not just write
unit tests", pointed at writing quality.

## Where the deterministic checker runs out

It catches **18 of 20** labeled defects (`uv run wealth evals self-test`). The
two misses are the most useful part of the workshop:

- **`u06-wrong-denominator`** — the memo says `34.71% of the portfolio`. That
  number is real; it came from `sector_exposure`, which measures share of
  *equity*, not of the portfolio including cash. Every digit checks out and the
  sentence is false. No number-matcher can see this. **This is what an LLM judge
  is for.**
- **`u08-unsourced-percentage`** — the memo invents `up 12%`. Nothing computed
  it. But the ledger holds `percent_of_spend: 11.88`, which rounds to 12, so the
  tolerance that correctly grounds `$18,421` for `18420.55` lets it through.
  Tighten the tolerance and honest reformatting starts failing. **There is no
  setting that avoids both**, and knowing that is worth more than a better
  threshold.

---

## Layout

| Path | What it is |
|---|---|
| `Makefile` | The front door. `make` lists everything. |
| `src/wealth_agent/agents/` | One file per agent. The directory listing is the architecture. |
| `src/wealth_agent/agents/supervisor.py` | Every harness decision, in one readable file |
| `src/wealth_agent/prompts/` | Every prompt as a reviewable, diffable file |
| `src/wealth_agent/policy.py` + `policies/` | What the portfolio is *supposed* to look like |
| `src/wealth_agent/tools/allocation.py` | Drift, cash runway, and the exact trade sizes |
| `src/wealth_agent/models.py` | Caching, model tiering, and the cost meter |
| `src/wealth_agent/reporting/render.py` | The HTML report, with every figure annotated |
| `src/wealth_agent/middleware/verification_gate.py` | The free check that short-circuits the paid one |
| `src/wealth_agent/voice.py` | A deterministic lint for machine-sounding prose |
| `src/wealth_agent/verify.py` | The deterministic checks. No LLM. |
| `src/wealth_agent/data/store.py` | Run workspace + append-only grounding ledger |
| `src/wealth_agent/middleware/grounding_ledger.py` | Records every tool result. ~90 lines. |
| `src/wealth_agent/mcp_servers/auth.py` | OAuth 2.1 + DCR + PKCE from a headless process |
| `src/wealth_agent/mcp_servers/clients.py` | Tool partitioning by blast radius, failing closed |
| `src/wealth_agent/data/adapters.py` | Live schema → fixture contract. Where the arithmetic lives. |
| `src/wealth_agent/mcp_servers/replay_server.py` | A real MCP server over fixtures — not a mock |
| `src/wealth_agent/evals/fixtures.py` | 20 memos with human labels |
| `src/wealth_agent/evals/judge_alignment.py` | **Loop 0** |
| `skills/` | Progressive-disclosure skills: memo format, memo voice, humanizer (vendored, MIT), category rulebook, verification protocol |
| `AGENTS.md` | Agent memory + MCP reference discipline |
| `artifacts/runs/` | Four frozen runs, so every demo step works offline |
| `WORKSHOP_BRIEF.md` | Topic, audience, objectives, curriculum placement |
| `FACILITATOR.md` | Timed run-of-show with talking points and Q&A prep |

---

## What verification costs

Measured, on the same question and the same data:

| | first working version | now |
|---|---|---|
| tokens | 1,782,764 | **554,000** |
| wall clock | 17.6 min | **3.8 min** |
| cost | ~$7.50 | **$0.69** |
| grounding | 98.6% | **100%** |
| claims needing a human | 2 | **0** |

Four changes got there, and none of them traded away quality:

1. **Prompt caching** — 83–90% of input tokens now come from cache at a tenth
   of the price. Measured, not assumed: `make cost` prints the hit rate, because
   caching fails *silently* and an unmeasured cache is an assumption.
2. **Model tiering** — the agents whose job is "call four deterministic tools
   and report what they said" run on a smaller model. The arithmetic comes from
   Python either way. The allocation strategist is deliberately **not** tiered:
   it is the one agent producing a judgement a human acts on with money.
3. **The model stopped retyping tables.** The memo carries `{{table:drift}}`
   and the report expands it from recorded data. A table the model retypes
   costs output tokens on every revision and can be wrong; a placeholder can be
   neither.
4. **The free check runs first.** The deterministic verifier is a few
   milliseconds and no model. On a clean memo the LLM verification path never
   executes at all.

That last one is this repo's own argument turned on itself, and getting it wrong
was instructive: the first attempt *added* the deterministic gate alongside a
verifier subagent the prompt delegated to unconditionally and a rubric grader
that ran every time. Three verification systems, each asking for its own
revision, made a run **40% more expensive than the version it was meant to
improve**. Order your checks by cost and let the cheap ones short-circuit —
`--always-judge` turns the LLM loop back on when you want to demonstrate it.

Verification is still not free, and whether the trade is worth making depends
entirely on what happens at your company when a memo is wrong. You can only have
that argument once both numbers are on the table.

## Running it on a real account

Everything above is fixtures. `--live` talks to Robinhood, and three things
about that deployment are worth knowing before you try:

```bash
uv run wealth auth login       # OAuth 2.1 + DCR + PKCE, browser, once per server
uv run wealth mcp probe --live # 54 trading tools, 7 banking, split by blast radius
uv run wealth run --live
```

**The two servers live on different hosts.** `agent.robinhood.com/mcp/banking`
answers, but advertises its canonical resource as
`banking-agent.robinhood.com/mcp/banking`, and the MCP SDK enforces RFC 9728
resource matching — so the convenient URL fails the flow with *"Protected
resource … does not match expected …"*. Always use the identifier the server
names in its own metadata.

**One server failing does not end the run.** Banking rejects a *cached* token
from a dynamically-registered client (`401 client id not allowed`) and the SDK
re-runs the browser flow, which usually completes silently. If it doesn't, that
server alone degrades to fixtures and the run says so in a yellow banner —
because "some of this memo is about your real money and some of it is invented"
must never be something you infer from a quiet log line.

**Live and fixture schemas disagree, so there is an adapter.** Live positions
carry no market value at all: `get_equity_positions` returns what you own and
what you paid, today's price lives in `get_equity_quotes`, and the sector lives
in `get_equity_fundamentals`. [`adapters.py`](src/wealth_agent/data/adapters.py)
joins the three and computes the rest, which keeps the arithmetic in tested
Python instead of in a model's head. Checked against Robinhood's own
`equity_value`, the adapter's independently-summed position values agree to
**$0.00**.

One caveat: the banking server exposes the **agent virtual card** only, not a
personal Robinhood card. If you have never used an agent card, live spend
analysis is correctly empty.

---

## Safety

This connects to real brokerage MCP servers, so:

- **`DEMO_MODE=1` is the default.** You opt *in* to touching a real account,
  never out of it. Presentations run in demo mode.
- **Write tools have two independent switches.** `ALLOW_WRITE_TOOLS` controls
  whether the model can see order placement at all; `interrupt_on` controls
  whether it can execute without a human. "The model can see it" and "it happens
  without me" are different risks.
- **Unknown tools are classified as writes.** When Robinhood ships
  `place_options_order`, an allowlist would admit it silently. The classifier
  fails closed. See `MUTATION_VERBS` in `mcp_clients.py`.
- **Some reads are worse than writes.** `banking_get_agent_card_creds` returns a
  card's PAN, expiry and CVV. No verb in that name mutates anything, so
  read/write partitioning hands it straight to the model — and from there to a
  context window, a checkpoint, and a trace. Blast radius is not two-valued:
  "can this change my account?" and "can this leak something I cannot un-leak?"
  are different questions. Secret-bearing tools are a third category with no
  flag that enables them. See `SECRET_TOKENS`.
- **Recorded evidence is append-only to the model.** Filesystem permissions deny
  writes to `/sources/` and the ledger. An agent that can edit its own evidence
  can make any claim verify, which would make this whole exercise theatre.
- **Nothing real reaches git.** Raw captures go to `captures/` (gitignored);
  only synthetic fixtures are committed.

---

## Credit

Prose guidance comes from the [Humanizer skill](https://github.com/blader/humanizer)
by [blader](https://github.com/blader), MIT licensed, vendored at
`skills/humanizer/SKILL.md` with its frontmatter and attribution intact. It is
in turn based on [Wikipedia: Signs of AI writing](https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing),
maintained by WikiProject AI Cleanup.


The principle this workshop enforces — *"the LLM reasons, deterministic code
computes"* — is stated in the README of a personal-finance agent by
[Kaushik Ghosh](https://github.com/skiingfalcon/personal-finance-agent), built
on a different stack. Arriving at it independently and then finding it already
written down was a good sign. This repo shares no code with it; what it adds is
the part that makes the principle enforceable rather than aspirational.
