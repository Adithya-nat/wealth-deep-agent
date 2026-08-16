# Trust, but Verify

A deep agent that writes a grounded personal-wealth memo — and the three loops
that prove it.

Workshop material for a 45-minute session. See **[WORKSHOP_BRIEF.md](WORKSHOP_BRIEF.md)**
for the topic, audience, objectives, and where it sits in a curriculum.

Everything below runs **offline against fixtures**. No API keys, no accounts,
no network, no real financial data.

---

## Setup

```bash
uv sync
uv run pytest            # 31 tests, ~0.1s, no keys needed
```

To run the agent itself you need one key:

```bash
cp .env.example .env     # then set ANTHROPIC_API_KEY
```

---

## The measured result

Three configurations. Same subagents, same tools, same data, same question.

| configuration | grounding | citations | fabricated | ships? |
|---|---|---|---|---|
| **naive** — no skills, no rules, no verification | 90.9% | **0** | 0 | ✗ |
| **baseline** — skills + grounding rules, nothing checks them | 99.2% | 32 | 0 | ✓ |
| **verified** — + verifier subagent + runtime rubric loop | 98.6% | 42 | 0 | ✓ |

Sit with the naive memo. It made ~20 claims about Apple's China antitrust
exposure, EU DMA fines, and tariff guidance and attributed **none** of them. It
rounded six real tool outputs into tidier numbers. And it is comfortably the
**best-written** of the three — the only one that closes with a prioritized
action plan. The most persuasive memo was the least defensible, and reading it
carefully would not have told you.

All four runs below are frozen in `artifacts/runs/`, so every number here is
reproducible with the network unplugged:

```bash
uv run wealth artifacts list
uv run wealth verify --artifact naive
```

## The demo, one command per beat

Steps 1–5 need no keys and no network.

```bash
# 1. What can the agent see? Note the read/write split.
uv run wealth mcp probe

# 2. The "before": a memo that reads beautifully and cites nothing.
uv run wealth verify --artifact naive

# 3. The bug I shipped, and what it cost. See below.
uv run wealth verify --artifact ledger-bug     # 79.28%
uv run wealth verify --artifact baseline       # 99.24%

# 4. What the agent actually observed. The ledger is the evidence.
uv run wealth inspect --artifact baseline

# 5. Are the fixture labels themselves trustworthy?
uv run wealth evals self-test

# --- from here on, keys are required ---

# 6. Loop 0 — measure the judge before believing it.  (OPENAI_API_KEY)
uv run wealth evals judge-align

# 7. Loop 2 — datasets and experiments.               (LANGSMITH_API_KEY)
uv run wealth evals dataset
uv run wealth evals naive --concurrency 2
uv run wealth evals verified --concurrency 2

# 8. Loop 1 — runtime verification, live.             (ANTHROPIC_API_KEY)
uv run wealth run --mode verified
```

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

## The bug this repo caught in itself

`GroundingLedgerMiddleware` was installed on the supervisor only. Declarative
subagents don't inherit parent middleware — they're compiled with their own
stack — so every tool call made *inside* a subagent never reached the ledger.

Nothing errored. The agent ran beautifully. The verifier reported `$18,420.55`
— the actual cash balance, straight out of `get_account_balances` — as
unsupported, because the evidence had been destroyed at the boundary between two
context windows. Which is the exact thesis of this workshop.

One-line fix: **79.28% → 99.24%**. The before-run is frozen as
`artifacts/runs/ledger-bug`, and `tests/test_ledger_middleware.py` asserts on
the configuration so it cannot silently return.

The lesson generalizes past the bug: **in a multi-agent system, cross-cutting
concerns have to be installed per agent.** Logging, redaction, rate limiting,
and audit trails all have this shape, and all of them fail silently the same way.

---

## Architecture

```
supervisor                       create_deep_agent
│  TodoListMiddleware · GroundingLedgerMiddleware · RubricMiddleware
│  skills · memory · filesystem permissions · interrupt_on(writes)
│
├── portfolio-analyst    DEEP    Robinhood Trading MCP → /portfolio/
├── spend-analyst        DEEP    Robinhood Banking MCP → /spend/
├── market-researcher    DEEP    web search + fetch    → /sources/
└── verifier          NOT DEEP   two deterministic checks, nothing else
```

**A subagent is a context window with a job.** Give it a deep-agent harness only
when the job is open-ended. The verifier's job has exactly one right answer, so
it is a plain `create_agent` ReAct loop — no filesystem it would not use, no
`task` tool it must not use, no planning, no latency. Reaching for
`create_deep_agent` a fourth time would have been the easy call and the wrong
one. See [`src/wealth_agent/subagents.py`](src/wealth_agent/subagents.py).

### The idea the rest hangs on

> **You cannot evaluate what you did not record.**

An agent that reads a balance, compresses it through a subagent, and writes a
memo has destroyed the link between the number on the page and the API response
it came from. So [`GroundingLedgerMiddleware`](src/wealth_agent/ledger_middleware.py)
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
| `src/wealth_agent/supervisor.py` | Every harness decision, in one readable file |
| `src/wealth_agent/subagents.py` | Four subagents, and why one is deliberately shallow |
| `src/wealth_agent/verify.py` | The deterministic checks. No LLM. |
| `src/wealth_agent/store.py` | Run workspace + append-only grounding ledger |
| `src/wealth_agent/ledger_middleware.py` | Records every tool result. ~90 lines. |
| `src/wealth_agent/mcp_auth.py` | OAuth 2.1 + DCR + PKCE from a headless process |
| `src/wealth_agent/mcp_clients.py` | Tool partitioning by blast radius, failing closed |
| `src/wealth_agent/replay_server.py` | A real MCP server over fixtures — not a mock |
| `src/wealth_agent/evals/fixtures.py` | 20 memos with human labels |
| `src/wealth_agent/evals/judge_alignment.py` | **Loop 0** |
| `skills/` | Progressive-disclosure skills: memo format, rulebook, protocol |
| `AGENTS.md` | Agent memory + MCP reference discipline |
| `artifacts/runs/` | Four frozen runs, so every demo step works offline |
| `WORKSHOP_BRIEF.md` | Topic, audience, objectives, curriculum placement |
| `FACILITATOR.md` | Timed run-of-show with talking points and Q&A prep |

---

## What verification costs

A verified run: **1,782,764 tokens, 17.6 minutes**. The naive run is a fraction
of that. Verification is not free, and this repo does not pretend otherwise —
whether the trade is worth making depends entirely on what happens at your
company when a memo is wrong. You can only have that argument if both numbers
are on the table.

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
- **Recorded evidence is append-only to the model.** Filesystem permissions deny
  writes to `/sources/` and the ledger. An agent that can edit its own evidence
  can make any claim verify, which would make this whole exercise theatre.
- **Nothing real reaches git.** Raw captures go to `captures/` (gitignored);
  only synthetic fixtures are committed.

---

## Credit

The principle this workshop enforces — *"the LLM reasons, deterministic code
computes"* — is stated in the README of a personal-finance agent by
[Kaushik Ghosh](https://github.com/skiingfalcon/personal-finance-agent), built
on a different stack. Arriving at it independently and then finding it already
written down was a good sign. This repo shares no code with it; what it adds is
the part that makes the principle enforceable rather than aspirational.
