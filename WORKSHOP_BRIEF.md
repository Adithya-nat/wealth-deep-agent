# Workshop Brief — Trust, but Verify

**Making a deep agent's output provable**

Adithya Natarajan · 45 minutes + 15 minutes Q&A · Python, Deep Agents, LangSmith

---

## Topic & rationale

Most teams I meet are past "can we build an agent." Their agent works. It plans,
it delegates, it produces something that reads well. They are stuck one step
later, on a question nobody's tutorial answers:

> **How do you know it's right — and how do you know that next week, after
> someone changes a prompt?**

This workshop answers that, on a deep agent that produces a financial memo. We
build three loops:

| Loop | Where it runs | What it catches |
|---|---|---|
| **1 — runtime** | inside the agent | Defects in *this* run, before a human sees them. `RubricMiddleware` with a deterministic verifier handed to the grader as a tool. |
| **2 — offline** | outside the agent | Regressions across runs. LangSmith datasets and experiments, so "did that change help?" has a number. |
| **0 — the evaluator** | on the judge | An LLM judge is an LLM application. Measure it against human labels *before* you believe a single score it produces. |

Loop 0 is the one almost nobody builds, and it is why so many teams have eval
dashboards they quietly ignore. Measured on our own 20 labeled fixtures, with
the same judge model throughout:

| judge prompt | agreement | blessed a bad memo | flagged a good memo |
|---|---|---|---|
| v1, a reasonable first draft | 85% | **2** | 1 |
| v2, strict — written after reading v1's mistakes | 85% | **0** | 3 |
| v3, calibrated | **95%** | **0** | 1 |

Look at v2. By the headline number it was a *complete waste of time* — 85%
before, 85% after. It had in fact eliminated the entire dangerous error class
and introduced a milder one. **A single agreement number would have told you to
throw away the change that mattered most.** That is the session's central
demonstration, and it is why "we have evals" and "we can trust our evals" are
different claims.

The domain is personal wealth — a portfolio and card feed reached through
Robinhood's MCP servers — because it makes the stakes legible. An uncited claim
in a client-facing financial memo is not a rough edge. It is a finding.

**Why this and not "how to build a deep agent":** that is a tutorial, and
LangChain already ships a good one. Every team in the room can already build the
agent. The gap between their prototype and production is not capability, it is
evidence.

---

## Target audience & prerequisites

**Who this is for:** a platform or AI engineering team (5–15 people) at a
financial-services or otherwise regulated company, who have an agent working in
staging and cannot get sign-off to ship it.

**Prerequisites:**

- Comfortable with Python — you should be able to read a decorator and an async
  function without stopping.
- Have built at least one tool-calling agent, in any framework. You know what a
  tool call is and why the model sometimes picks the wrong one.
- **No prior LangChain, LangGraph, LangSmith, or Deep Agents experience
  required.** Roughly eight minutes covers what the harness gives you; the rest
  of the session is framework-agnostic reasoning that happens to be expressed in
  this stack.

**Explicitly not required:** finance knowledge (every domain term is defined in
passing), evaluation or ML background, prior LangSmith account.

**What you need to follow along:** Python 3.12, `uv`, and the repo. Everything
runs offline against fixtures — no API keys, no accounts, no network.

---

## Learning objectives

By the end, a participant can:

1. **Locate an agent failure in a trace.** Specifically, find the point where a
   subagent's summary lost the link between a claim and its source — the class
   of bug that is invisible in the final output and obvious in the span tree.
2. **Design an agent so its claims are mechanically checkable.** Record every
   tool result and fetched source at the moment it arrives, via middleware
   rather than inside each tool, so no future tool can forget to participate.
3. **Build a three-tier evaluator stack and know which tier answers which
   question.** Deterministic code where there is one right answer; an LLM judge
   only where there is not; trajectory checks for what the agent *did* rather
   than what it *said*.
4. **Measure a judge before trusting it.** Build a labeled set, compute
   agreement, read the disagreements, and improve the prompt from evidence
   rather than intuition.
5. **Turn a score into a gate.** Wire a threshold into `pytest` so a
   groundedness regression fails CI the same way a broken import does.

A participant should leave able to do (2) and (4) on their own agent on Monday.
Those are the two that transfer regardless of framework.

---

## Where this fits

**Before this module** (assumed, not taught here):

- *Agent fundamentals* — tools, structured output, `create_agent`.
- *Deep agents* — subagents, planning, context isolation, filesystem offload.
  We use all of it and explain the choices, but we do not teach it from zero.

**This module:** the bridge from "it works" to "we can defend it."

**After this module:**

- *Deploying and operating agents* — Agent Server, human-in-the-loop at scale,
  **online** evaluations and alerting, annotation queues, feedback loops from
  real users. Loop 2 offline becomes Loop 2 continuous.
- *Cost and latency engineering* — the verification loop we add here roughly
  doubles the tail latency of a run. That is a real trade and deserves its own
  session.

It also works as the second half of a full-day workshop whose first half is deep
agent construction. That is the pairing I would recommend to a customer: build
it in the morning, learn to trust it in the afternoon.

---

## Anticipated friction points

**1. "Why not just write unit tests?"** — asked early, every time. The honest
answer is that you should, and we do: the deterministic checks *are* unit tests.
The distinction is between **invariants** (this citation resolves — assert it)
and **distributions** (the memo is well-grounded — score it, watch it move).
Teams get stuck because they try to assert on a distribution, watch it flake,
and conclude agents are untestable. I address this at minute four, before the
question is asked, because the room cannot hear anything else until it is
settled.

**2. "An LLM grading an LLM is turtles all the way down."** — the objection that
decides whether the session lands. Two answers, in order. First: *don't use a
judge where code will do* — we show the deterministic checker catching 18 of 20
labeled defects for free, and only then reach for a judge. Second: *the judge is
measurable*, and we measure it live. The 60%→90% moment is the answer to this
objection, which is why it sits in the middle of the session rather than at the
end.

**3. Trace overwhelm.** A single run is 40+ spans across four agents. Dropped
into LangSmith cold, people scroll and disengage. I teach the
`lc_agent_name` metadata filter *first*, so the first thing anyone does is
collapse the tree to one subagent. Navigation before content.

**4. Building a dataset feels like the boring homework.** It is the step teams
skip, and skipping it is why they have no evals. Countered by building one from
real traces in about three lines, and by being blunt that 20 examples beats
zero — perfectionism here is procrastination with better branding.

**5. Confusing the runtime loop with the offline loop.** Both use LLM-as-judge,
so they blur. They have different jobs: the runtime rubric protects *one run*
and costs latency on every request; the offline experiment measures *the system*
and costs nothing at request time. One slide, returned to twice.

**6. "Our data can't leave the building."** In a regulated room this arrives
within ten minutes and derails things if unanswered. Short version: self-hosted
LangSmith exists, input/output masking exists, and this demo runs entirely
offline. Ninety seconds, then back on track — with an offer to go deeper
afterwards.

---

## A note on the demo data

The workshop connects to Robinhood's Trading and Banking MCP servers, which are
real and use OAuth 2.1 with dynamic client registration. Getting a headless
Python agent through that flow is itself a useful five minutes, and the code is
in the repo.

The data you will see is **synthetic and deterministic**, served by a local MCP
server that mirrors the real tool surface. Not redacted — generated. Redaction
preserves shape but breaks arithmetic, and a workshop about numbers being
checkable cannot run on numbers that do not add up.

---

## The measured result

Three configurations, same subagents, same tools, same data, same question:

| configuration | grounding | citations | fabricated | ships? |
|---|---|---|---|---|
| **naive** — no skills, no rules, no verification | 90.9% | **0** | 0 | ✗ |
| **baseline** — skills + grounding rules, nothing checks them | 99.2% | 32 | 0 | ✓ |
| **verified** — + verifier subagent + runtime rubric loop | 98.6% | 42 | 0 | ✓ |

The naive memo is the one to sit with. It made roughly twenty claims about
Apple's China antitrust exposure, EU DMA fines, and tariff guidance — and
attributed **none** of them. It also quietly rounded six real tool outputs into
tidier numbers. And it is, by a distance, the **best-written** of the three: it
closes with a prioritized action-items table. The most persuasive memo was the
least defensible one, and no amount of reading it carefully would have told you.

---

## What we are honest about

A workshop that only shows the happy path teaches people to be surprised in
production. Four things I say out loud:

- **The deterministic checker has a hole, and we look straight at it.** Its
  rounding tolerance correctly grounds `$18,421` for `18420.55` — and also lets
  a fabricated `12%` through, because a real `11.88%` rounds to 12. Tighten the
  tolerance and honest reformatting starts failing. There is no setting that
  avoids both. That trade-off is the most useful thing in the session.
- **Verification is not free, and here is the bill.** A verified run cost
  **1.78M tokens and 17.6 minutes**; the naive one was a fraction of that.
  Whether that trade is worth making depends entirely on what happens when you
  are wrong — which is a decision for the room, not for me.
- **I shipped this bug while building it.** `GroundingLedgerMiddleware` was
  installed on the supervisor only. Declarative subagents don't inherit parent
  middleware, so every tool call made *inside* a subagent never reached the
  ledger. Nothing errored. The agent ran beautifully. The verifier reported the
  actual cash balance — straight from `get_account_balances` — as unsupported,
  because the evidence had been destroyed at the boundary between two context
  windows. That is the exact thesis of this workshop, and the system caught it
  on itself. Grounding went **79.3% → 99.2%** on the fix, and the frozen
  before-run is in the repo as `artifacts/runs/ledger-bug`.
- **One flagged figure was my API's fault, not the model's.** `summarize_period`
  returned a percentage change but not the absolute change, so the agent — quite
  reasonably wanting to report the dollar delta — computed it itself, and got
  flagged. The fix was to return the number, not to scold the model. When
  verification flags a figure, ask first whether some tool should have returned
  it.
