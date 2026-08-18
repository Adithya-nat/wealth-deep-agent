# Facilitator run-of-show

45 minutes delivery + 15 Q&A. Every command below runs offline against frozen
artifacts. **Nothing in this session requires a live model call.**

> **`make demo` walks this whole script**, one keypress per beat, running each
> command for you. Use it and this document becomes your notes rather than your
> checklist — which also means the script cannot drift from what the commands
> actually do.

## Before you start

```bash
make setup       # install, then pre-flight
make doctor      # keys, prompts, policy, fixtures — all green?
```

Windows to have open: terminal, editor on `src/wealth_agent/`, LangSmith on the
`wealth-deep-agent` project. Close everything else — a Slack notification during
the trace walkthrough costs you the room.

**If the network dies:** nothing changes. Say so out loud when it happens; it
makes the point about demo discipline better than a slide would.

---

## 0:00–0:04 — The ship gate

Open the naive report in a browser — `uv run wealth report --artifact naive`.
Scroll it slowly. Let them read the action-items table at the bottom, and notice
that every figure on the page is underlined and clickable.

> "This is a wealth memo an agent wrote. Compliance signs off Monday. You're the
> tech lead. What do you need before you say yes?"

Take answers. Someone will say "check the numbers." Ask *how*. There are 66
figures and about twenty external claims.

Then:

```bash
make demo   # beat 1, or: uv run wealth report --artifact naive
```

**Land it on `citations checked: 0`.** Not the grounding score — the zero.

> "It made twenty claims about Apple's China antitrust exposure and EU tariff
> guidance. Zero are attributable. And this is the best-written memo of the
> three you'll see today — it's the only one with an action plan. The most
> persuasive one was the least defensible, and reading it carefully would never
> have told you that."

---

## 0:04–0:13 — The architecture

Open `src/wealth_agent/agents/supervisor.py`. Scroll the docstring, then the
`create_deep_agent` call at the bottom. One screen, every decision visible.

```bash
uv run wealth mcp probe
```

Point at the read/write split.

Then `ls src/wealth_agent/agents/` — one file per agent, so the directory
listing is the architecture. Open `verifier.py` and `allocation_strategist.py`
side by side: two agents that are deliberately *not* deep agents.

> "A subagent is a context window with a job. Three of these are deep agents
> because their work is open-ended. The verifier is a plain `create_agent` ReAct
> loop, because its job has exactly one right answer. Giving it a deep-agent
> harness would have added a filesystem it won't use, a `task` tool it must not
> use, and latency. Reaching for `create_deep_agent` a fourth time was the easy
> call and the wrong one."

**If someone asks "why deep agents at all?"** — one sentence: filesystem for
context offload, subagents for context isolation, todos for planning. Don't
teach the framework; they can read the docs. Move on.

**The prompts are files.** `ls src/wealth_agent/prompts/` — and the naive/careful
comparison you are about to make is `diff supervisor.md supervisor_naive.md`,
two files that differ only in the discipline they impose. Worth ten seconds:
a prompt in a Python string cannot be diffed, reviewed, or owned by the
compliance person whose wording it actually is.

---

## 0:13–0:19 — Read the trace

LangSmith → `wealth-deep-agent` → the `wealth-supervisor` trace.

**Teach navigation first.** Runs view → Add filter → Metadata → `lc_agent_name`.
Show it collapse to one subagent.

> "Forty-plus spans across four agents. Nobody reads this top to bottom. The
> first thing you do is collapse it to the agent you suspect."

Then show the token count. The first working version of this run was **1.78M
tokens and 17.6 minutes**; it is now **554k tokens, 3.8 minutes, $0.69** at
higher grounding.

> "Hold onto both numbers. We'll come back to what the difference bought — and
> to the change I made that accidentally went the wrong way."

---

## 0:19–0:24 — Make it checkable

```bash
uv run wealth inspect --artifact baseline
```

The ledger table, grouped by agent. Then:

```bash
ls artifacts/runs/baseline/sources/
```

> "That's the agent's working memory. It's a directory. You can `ls` it."

Open `src/wealth_agent/middleware/grounding_ledger.py`.

> "The obvious way to build this is to have each tool write its own ledger
> entry. That works until someone adds the eleventh tool and forgets — and the
> failure is silent, because a missing entry doesn't raise, it just makes a true
> claim look fabricated. Middleware inverts it. There's no opt-in, so there's
> nothing to forget."

Then `src/wealth_agent/verify.py`. Two checks, ~40 lines, no model.

**The line to say:** *"You cannot evaluate what you did not record."*

---

## 0:24–0:29 — The bug I shipped

This is the strongest five minutes in the session. Do not skip it.

```bash
uv run wealth verify --artifact ledger-bug
```

76.58%. Point at `$18,420.55` reported as unsupported.

> "That's the cash balance. It came straight out of `get_account_balances`. It's
> real. So why did my own verifier call it unsupported?"

Let them think. Then:

> "I installed the recording middleware on the supervisor. Declarative subagents
> don't inherit parent middleware — they're compiled with their own stack. So
> every tool call made *inside* a subagent never reached the ledger. Nothing
> errored. The agent ran beautifully. The evidence was destroyed at the boundary
> between two context windows, which is the exact thing this whole system exists
> to catch. It caught it. On itself."

```bash
uv run wealth verify --artifact baseline    # 99.24%
```

> "One-line fix. 79 to 99. And the general lesson is bigger than the bug: in a
> multi-agent system, **cross-cutting concerns have to be installed per agent**.
> Logging, redaction, rate limiting, audit — all the same shape, all fail
> silently the same way."

---

## 0:29–0:36 — Loop 0: evaluate the evaluator

```bash
make judge
```

> "Twenty labeled memos. Before I grade anything with a model, I check the
> labels against the deterministic checker. It agrees on 18 of 20."

**The two misses are the lesson.** Open `evals/evaluators.py`, read the
"Where the deterministic check runs out" docstring.

- `u06`: `34.71% of the portfolio` — real number, from `sector_exposure`, which
  measures share of *equity*. Every digit checks out; the sentence is false.
  **This is what a judge is for.**
- `u08`: invented `12%` passes, because a real `11.88%` rounds to 12. **Tighten
  the tolerance and honest reformatting starts failing. No setting avoids both.**

Then the alignment table (pre-computed — do not run this live, it costs ~3
minutes and OpenAI calls):

| prompt | agreement | blessed a bad memo | flagged a good memo |
|---|---|---|---|
| v1 naive | 85% | **2** | 1 |
| v2 strict | 85% | **0** | 3 |
| v3 calibrated | **95%** | **0** | 1 |

> "Look at v2. By the headline number it was a total waste of time — 85 before,
> 85 after. It had actually eliminated the entire dangerous error class and
> introduced a milder one. A single agreement number would have told me to throw
> away the change that mattered most."

To run it live if asked: `uv run wealth evals judge-align` (~3 min).

---

## 0:36–0:42 — Loop 1 and Loop 2

**Loop 1 — runtime.** Open the `RubricMiddleware` block in `supervisor.py`.

> "The grader gets the deterministic checker *as a tool*. That turns 'does this
> look right?' into 'what does the check say?' — a much easier question with a
> much more stable answer."

**Loop 2 — offline.** LangSmith → Datasets → `wealth-agent-memo-questions` →
the two experiments → **Compare**.

**Now cash the cost number:**

```bash
make cost
```

> "Here is the bill, per agent, with the cache hit rate. This run cost about a
> dollar and change. The first version of this repo cost seven-fifty, and the
> difference is three things: caching the prefix, putting the agents whose job
> is 'call four tools and summarize' on a smaller model, and — the one that
> matters most — running the free deterministic check *first* so that on a clean
> memo the LLM verifier and the rubric grader never execute at all."

> "That last one is this workshop's own argument applied to its own control
> flow. I was calling a judge on every run, including the runs where code had
> nothing to complain about. The argument was right and my implementation
> didn't follow it."

Then the report footer:

> "142 claims checked by machine, 2 needed a human. **That ratio is the
> product.** Whether verification is worth it depends on what happens at your
> company when a memo is wrong — but you can only have that argument once both
> numbers are on the table."

**Loop 2 in CI:**

```bash
make test
```

> "72 tests, no keys, no network. The grounding threshold is one of them. A
> regression fails the build like a broken import."

---

## 0:42–0:45 — Where this fits

Curriculum slide. Before: agent fundamentals, deep agent basics. After:
deployment, online evals + alerting, annotation queues, cost/latency.

Close on:

> "Two things transfer regardless of framework. Record your evidence at the
> moment it arrives. And measure your judge before you believe it."

---

## What the buyer asks (as opposed to the developer)

The Q&A below this one is what a curious engineer asks. These are what the
person who has to approve the spend asks, and they arrive in roughly this order.
Answer them in their terms, not in yours.

**"What does this actually do?"** — It reads a brokerage account, six months of
card spending, and a written investment policy, and produces a memo that says
what to move and why. An analyst doing that by hand spends hours per client, and
most of it is mechanical. This does it in about five minutes.

**"How do I know it's right?"** — Every number in the memo is checked against the
tool output it came from, before anyone reads it. Open the report and click a
figure: it names the tool that produced it. On the last run, 142 claims were
checked automatically and 2 needed a person. *That ratio is the product.* The
memo being fast is worth much less than the memo being checkable — without the
second part a human has to re-derive everything, and you have moved the work
rather than removed it.

**"What happens when it's wrong?"** — Two different failures, deliberately
labelled differently. *Unsupported* means nothing recorded backs a figure; it
may well be true and nobody can currently tell. *Fabricated* means it cites a
source that does not say that — a citation to a page never fetched, or a quote
that is not in the page it is attributed to. The first is a question for a
reviewer. The second is a defect. Collapsing them into one "fail" throws away
the only signal that tells you which is which.

**"What does a run cost?"** — About a dollar, and `make cost` breaks it down per
agent with the cache hit rate. Every agent also has a hard ceiling on model
calls, so the answer to "how expensive can one run get?" is a number rather than
a shrug.

**"Can it touch my money?"** — Not without you. Demo mode is the default: you opt
*in* to a real account, never out of it. Order placement has two independent
switches — one controls whether the model can see those tools at all, the other
controls whether they can execute without a human — because "the model can see
it" and "it happens without me" are different risks. And unknown tools are
classified as writes, so a new order type shipped by the broker is gated by
default rather than admitted silently.

**"Our data can't leave the building."** — Self-hosted LangSmith exists,
input/output masking exists, and this entire demo runs offline against
generated data. Ninety seconds, then offer to go deeper afterwards — this
question derails a session if you let it expand in the room.

**"How long until this works on our agent?"** — Two weeks, in this order. Week
one: record every tool result via middleware, build a twenty-example labeled set
from real traces, get one deterministic check into CI. Week two: add a judge
only where code cannot reach, measure it against the labels, put a threshold on
the merge. The order is the advice — most teams start with the judge and never
build the substrate underneath it.

**"What would you cut if we had one day?"** — The judge. Record evidence, write
one deterministic check, wire it to CI. That is the eighty percent, and it is
the part that still works when you change models.


## Q&A prep

**"Why not just unit tests?"** — You should, and we do; the deterministic checks
*are* unit tests. The distinction is invariants (assert) vs. distributions
(score and watch). Teams get stuck asserting on distributions, watch it flake,
and conclude agents are untestable.

**"LLM grading LLM is turtles all the way down."** — Two answers, in order:
don't use a judge where code will do (18/20 for free), and the judge is
measurable — here's the table.

**"How many labels do we need?"** — Twenty beats zero, and you can build them
from real traces in an afternoon. Balance them; an unbalanced set lets a judge
score well by guessing. Add more when you see the judge disagree in a new way.

**"Who labels them?"** — Whoever would have to defend the output. In finance
that's a compliance reviewer, not an engineer. That's a feature: it's the
cheapest way to find out that your definition of "correct" and theirs differ.

**"What happens when the model upgrades?"** — Re-run alignment. It's a fixture
suite; it costs minutes. A judge validated against GPT-5.5 has no standing on
GPT-6.

**"Our data can't leave the building."** — Self-hosted LangSmith, input/output
masking, and this entire demo ran offline. Ninety seconds, offer depth after.

**"Could you drop the LLM judge entirely?"** — For this agent, almost. 18/20 is
a lot. But `u06` — the wrong-denominator case — is a semantic error no
number-matcher can see, and in a regulated memo that's exactly the class you
can't miss. The judge earns its place on a narrow set of questions, which is
also why it should run second.

**"Why is the verifier not a deep agent?"** — Its job has one right answer and
takes two tool calls. A harness would add a filesystem it won't use, a `task`
tool it must not use, and latency. Reflexively giving every component the most
capable harness is how agent systems get slow and unpredictable.

**"How would you roll this out to our team in two weeks?"** — Week one: instrument
and record; build a 20-example labeled set from real traces; get one
deterministic check in CI. Week two: add a judge only where code can't reach,
align it, and put a threshold on the merge. The order matters — most teams start
with the judge and never build the substrate.

**"What would you cut if we had one day?"** — The judge. Record evidence, write
one deterministic check, wire it to CI. That's the 80%, and it's the part that
still works when you change models.
