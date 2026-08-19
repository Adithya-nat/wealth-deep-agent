# Presenter Scripts — *Trust, but Verify*

**Wealth Agent · 13 slides · 45 minutes + 15 minutes Q&A**
Adithya Natarajan

---

## How to use this

Each slide has three things:

- **Timing** — the budget. If you are over on a slide, take it out of slide 5 or 12, never out of slide 11.
- **Script** — say it roughly like this. It is written to be spoken, not read. Short sentences on purpose.
- **If asked here** — the objection this slide reliably attracts, and the answer.

Speak at about 130 words a minute. Every script below is sized for its budget with slack built in. Pauses are marked `//`.

Two rules for the whole hour:

1. **Never say a number you cannot show.** Every figure in these scripts is reproducible from `artifacts/runs/` — see the pre-flight warning below, because right now those directories are empty.
2. **When you name a LangChain concept, name the API.** Say `create_deep_agent`, not "the deep agent thing." That is the difference between someone who has read the docs and someone who has shipped on them.

---

## ⚠ Before you present — read this first

**`artifacts/runs/` is empty in this checkout.** All five run directories (`naive`, `baseline`, `verified`, `recommended`, `ledger-bug`) exist but contain nothing except a zero-byte `ledger.jsonl`. They are not tracked in git. As it stands:

- Slide 8 — **all four demo beats cannot run.**
- Slide 9 — the `artifacts/runs/ledger-bug` diff cannot be shown.
- "If the wifi dies this demo still works" is **not currently true.**

**Fix before the interview:**

```bash
make doctor                        # keys, prompts, policy, fixtures
uv run wealth run "..."            # generate each configuration
uv run wealth artifacts save       # freeze it into artifacts/runs/
make report                        # confirm it opens
make compare                       # confirm the table recomputes
git add -f artifacts/runs && git commit    # they are currently untracked
```

Then rehearse the click-through on slide 8 twice. If you cannot regenerate them in time, cut slide 8's demo to `slides/trust-but-verify.html` and **say out loud** that you're showing a recorded walkthrough — do not narrate a live demo you aren't running.

**Two other traps:**

- **`make judge` is mislabelled.** The Makefile calls it "Loop 0 — measure the judge against human labels," but it runs `wealth evals self-test`, which only checks the fixture labels against the *deterministic* checker. The v1/v2/v3 table on slide 11 comes from **`uv run wealth evals judge-align`**. Don't type `make judge` on stage expecting slide 11's numbers.
- **The repo disagrees with itself on the ledger-bug score.** `README.md` and `FACILITATOR.md` say **76.58%**; `FACILITATOR.md` line 157 and `cli/demo.py` say **79%**. The deck and these scripts use 76.58% (the majority and the primary source). Reconcile the repo before someone diffs it in front of you.

---

## Slide 1 — Title · 1 minute

> Thanks for having me.
>
> I'm going to spend the next forty-five minutes on one problem, and I want to say up front which problem it is — because it is not the one most agent talks are about.
>
> This is a wealth agent. It reads a brokerage account, six months of card spending, and an investment policy, and it writes the memo a financial advisor writes before a client review. It works. It's been working for a while. //
>
> The talk is not about building it. The talk is about the thing that happens after it works — when you take a memo that says *sell eleven thousand dollars of Apple* to somebody whose job is to sign off on it, and they ask you where that number came from.
>
> That question is called *Trust, but verify*, and answering it mechanically — for every claim, on every run — is what we're going to build.
>
> Quick calibration before I start. Show of hands: who has an agent working in staging right now that you can't get shipped? // Right. That's the room this is written for.

**If asked here:** *"Do we need LangChain experience?"* — No. Four minutes on slide 5 covers what the harness gives you. Everything after that is framework-agnostic reasoning that happens to be written in this stack.

---

## Slide 2 — What this is · 2.5 minutes

> Before the topic, the thing itself. It's much easier to argue about how you verify something once you can see what's being verified.
>
> A **wealth memo** is a document an advisor writes before sitting down with a client. It says four things: here is what you own, here is where your money went, here is what I think you should change, and here is the evidence for each of those claims. Producing one by hand is several hours, and honestly most of those hours are not judgment. They're reconciliation, categorisation, computing weights against a policy, deriving trade sizes, chasing sources, formatting.
>
> **What goes in.** Four inputs. Positions and balances — that's the Robinhood Trading MCP server. Six months of card transactions — the Banking MCP server. Both of those come in as ordinary LangChain tools; MCP is just a transport, and once they're loaded the agent doesn't know or care that they came over a protocol. Third, an investment policy — a checked-in JSON file with the target allocation, the tolerance bands, and a cash floor. It's a file on purpose, so a compliance officer can read it and a diff can show you when it changed. And fourth, live market context from web search, where every page that gets fetched is written to disk.
>
> **What comes out.** Not "consider reducing your technology exposure." This: // *trim Apple by eleven thousand seven hundred fifty-nine dollars and forty-eight cents, because Information Technology is sitting at thirty-four point seven one percent of equity against a twenty-five percent target with a five-point band.*
>
> That's specific enough to act on, which is exactly what makes it dangerous. So two design commitments. Every dollar figure on that line was computed by a Python function, not by a model. And every market claim carries the id of a page that was actually fetched. In the report itself, every number is clickable — click it and you see which tool produced it and which agent was holding it at the time.
>
> And the line at the bottom is the customer's version of the value. Notice it has two halves, because there are two problems here, not one. // Hours of mechanical assembly become about four minutes and roughly a dollar — that's problem one. A hundred and forty-two claims checked automatically with two needing a person — that's problem two.
>
> I'll take them in that order, because **the second problem only exists once you've solved the first.**

**If asked here:** *"Is this real Robinhood data?"* — The servers are real, and they use OAuth 2.1 with dynamic client registration; getting a headless Python agent through that flow is in the repo and is a genuinely useful five minutes on its own. The data you'll see is synthetic and deterministic, served by a local server that mirrors the real tool surface. Generated, not redacted — redaction preserves shape but breaks arithmetic, and a session about numbers being checkable can't run on numbers that don't add up.

---

## Slide 3 — The problem the agent solves · 1 minute

> [Advance. Stop talking. Let them read it.]
>
> That is the problem this agent exists to solve. One sentence, and it has nothing to do with AI. //
>
> It's worth being specific about what those hours actually are, because almost none of them are judgment. Reconciling positions against the last statement. Categorising six months of card transactions into something you can reason about. Computing each holding's weight and comparing it against the policy. Working out the dollar size of every trade that would bring it back inside its band. Chasing down what's changed about the holdings. Then formatting the whole thing so a client can read it.
>
> The advisor's actual value is the judgment at the end — which drift matters, what to say, what to leave alone. // They spend most of their afternoon on everything *before* that.
>
> **That is the part the agent does.** About four minutes and a dollar instead of an afternoon. // And if this deck stopped right here, it would be a perfectly reasonable product pitch.

**Delivery note:** the silence at the start is the slide. Don't read the sentence out loud — they can read. And say "nothing to do with AI" deliberately: it signals you're selling an outcome, not a technology.

## Slide 4 — The catch · 3.5 minutes

> But it doesn't stop there, and this is the slide where the room leans in. // Because the hours only actually disappear if nobody has to put them back.
>
> A wrong figure in a client-facing financial memo is not a rough edge you fix next sprint. It is a compliance finding. It has a name, a paper trail, and somebody's quarter attached to it.
>
> So before anyone acts on that memo, three questions get asked. And I'd like you to answer them for me, as the customer. **Compliance signs off Monday. What do you need before you say yes?** // [take answers — write them up if there's a board]
>
> Good. They cluster into three. *Where did every number come from.* *Which source supports each market claim.* And *what stops the agent from making an unsafe change to the account.*
>
> Now look at the right-hand side, because this is the part that gets skipped. One memo carries about a hundred and forty-two figures and market claims. If the answer to "where did this number come from" is *a human re-derives it* — then I have built something that saves an analyst three hours and costs a reviewer three hours. // That is not a product. That is moving the work.
>
> And there's a specific trap here. Fluency and defensibility are produced by completely different mechanisms. Fluency you get free from the model. **Defensibility you have to engineer.** Which means the memo that reads best is the one you should trust least — and I'll show you that in about ten minutes.
>
> So the real deliverable was never the memo. It's the memo **plus the evidence that it's right**. Answering all three of those questions mechanically, on every run, with nobody re-deriving anything — that is the rest of this session.

**Note the slide has no LangChain tag.** That's deliberate — slides 3 and 4 are the customer's problem in the customer's words, and framework names on them would make it look like you led with the tooling. The tags start at slide 5, once the problem is agreed.

**Timing note:** the show-of-hands exchange is the most valuable ninety seconds in the deck — it makes the rest of the session *their* problem instead of yours. Do not cut it. Cut slide 5 instead.

## Slide 5 — Framework map · 3.5 minutes

> Four minutes of framework, then we're done with framework for the day. And I've ordered this table deliberately, smallest layer first, because the mistake I see teams make is starting at the top.
>
> **LangChain** is the agent framework. `create_agent` gives you the loop — model, tools, decide, repeat. `@tool` turns a Python function into something the model can call. Middleware is where you hook into that loop. And you get typed structured output, which matters more than it sounds like: it means a subagent can return a validated object rather than prose you have to parse.
>
> **LangGraph** is the runtime underneath. LangChain agents are LangGraph graphs — you don't have to know that until you need state that survives a crash, or streaming, or the ability to pause a run halfway through, get a human decision, and resume. Then you need it, and it's already there.
>
> **Deep Agents** is the opinionated harness on top. `create_deep_agent` gives you four things: planning, subagents, a filesystem, and skills. It is a set of defaults that somebody who has built a lot of these picked for you. And the reason I like it isn't that it's powerful — it's that the harness becomes *configuration*, so when you read my supervisor file, what you see is the **choices**, not the plumbing. That's an underrated property in a codebase somebody else has to maintain.
>
> **LangSmith** wraps all three. Traces so you can see what happened. Datasets and experiments so you can ask "did that change help" and get a number instead of a feeling. Evaluators, and monitoring in production.
>
> Now the line at the bottom, which is the actual point of the slide. // **Start at the smallest layer that solves the problem.** Two of the five subagents in my system deliberately don't use the deep-agent harness at all, and I'll defend that in a minute. Reaching for the most capable abstraction first is how you end up with an agent you can't reason about — you get autonomy in places where you didn't need autonomy, and every one of those places is a place it can surprise you.

**If asked here:** *"So do we need all four?"* — No. You need LangChain. LangGraph you already have, underneath. Deep Agents you reach for when the work is genuinely open-ended, and LangSmith you want on day one, because it's the only one of the four that tells you whether the other three are working.

---

## Slide 6 — Architecture · 4.5 minutes

> This is the whole system. A supervisor and five subagents — and the interesting thing about the diagram is that they are not all the same kind of agent.
>
> At the top, the **wealth supervisor** — `create_deep_agent`. It plans, it delegates, it writes the final memo. It's the only one that talks to the user.
>
> Three deep subagents underneath. **Portfolio analyst** — holdings and concentration. **Spend analyst** — six months of transactions. **Market researcher** — search, read, follow leads. These get the full harness, and here's the reason: their work is open-ended and context-heavy. The market researcher does not know in advance how many pages it needs to read. That's exactly the shape of problem subagents exist for. //
>
> And the reason is not "so it can do more work in parallel." It's context. The market researcher will burn a hundred thousand tokens reading pages about Apple's China exposure, and the supervisor should never see any of that. It should see a two-paragraph summary with source ids. **A subagent is a context window with a job** — that's the line I'd like you to leave with. You're not partitioning work, you're partitioning *attention*.
>
> Then two agents that are deliberately **shallow** — plain `create_agent` ReAct loops, no harness. The **allocation strategist** and the **verifier**. And I want to defend that, because it's the choice on this slide most likely to get pushed back on.
>
> The allocation strategist decides what to trade. That sounds like the most important judgment in the system, so why is it the least autonomous agent? // Because its arithmetic is not its job. The weights, the drift, the runway, the trade amounts — those all come out of deterministic tools. What the model contributes is judgment about *which* drift actually matters, and it returns a validated `RecommendationSet` through `response_format`. Bounded input, one correct output shape, no need to plan. A deep agent there would be strictly more surface area for no capability I need.
>
> Same for the verifier. It runs a fixed list of checks. Fixed lists don't need planning.
>
> And along the bottom: the **append-only grounding ledger**. Every tool result that becomes evidence — from the supervisor, the three analysts, and the strategist — recorded by `GroundingLedgerMiddleware`. // Note the words **per agent**, because we're coming back to that in about ten minutes and it's going to hurt.
>
> The verifier is the one agent that doesn't carry it, deliberately: it *reads* the ledger to check claims, it doesn't produce evidence for them. If I recorded its own tool calls into the thing it's auditing, I'd have built a closed loop.
>
> Append-only is load-bearing, by the way. The agent has real filesystem access. An agent that can rewrite `/sources/` can make any claim verify. Evidence is only evidence if the thing being audited can't edit it — so filesystem permissions make that directory read-only to the agent.
>
> [Open `src/wealth_agent/agents/supervisor.py` here. Scroll the docstring. Every decision on this slide is a named argument in one function call.]

**If asked here:** *"Why not make the verifier a deep agent too — wouldn't it catch more?"* — It would have more ways to *look*, and I don't want that. A verifier that can plan can decide to skip a check. The value of the verifier is that it is boring and identical every time. If it starts being creative, I've lost the property I built it for.

---

## Slide 7 — Design principle · 3.5 minutes

> This slide is the architecture thesis, and if you take one slide back to your own agent, take this one.
>
> Two columns. On the left, what the model decides. On the right, what code enforces. And the discipline is that **nothing crosses**.
>
> The model decides which specialist should investigate next. Which sources are worth reading and which leads to follow. And how to explain a trade-off — for instance, when the sector target says trim Apple and the single-name cap says trim VOO and those two pull against each other, a human has to be told that clearly. That is genuinely a language problem and the model is genuinely good at it.
>
> Code computes. Portfolio weights, drift against policy, cash runway, trade amounts. **Any number that reaches the memo comes out of a function.** If you're adding a capability to this system that produces a number, it is a tool — it is not an instruction in a prompt.
>
> Code records. Every tool result enters the append-only ledger, and it does that in **middleware**, not inside the tools. That distinction is the single highest-leverage decision in the repo, so let me be precise about why. If recording lives inside each tool, then recording is a habit — and a habit is something a new tool written by a new engineer in six months can forget. Nothing errors. The audit trail just quietly gets a hole in it. In middleware, no tool can forget to participate in an audit trail it doesn't know exists. **Move correctness out of the thing that gets copy-pasted and into the thing that wraps it.**
>
> And code gates. Citations resolve or the run is defective. Figures trace to a recorded tool result. Write tools cross a human. And the release gate is a `pytest` assertion on the verification report, so a defect fails the build the same way a broken import does.
>
> One clarification, because this slide gets misread: **deterministic does not mean "no AI."** It means this particular question has exactly one right answer, so ordinary software is the correct enforcement mechanism. Using a language model to check whether 34.71% equals 34.71% is not sophistication. It's just an expensive, less reliable `==`.
>
> Bottom line: `create_deep_agent` where the work is open-ended, `create_agent` plus `response_format` where it's bounded.

**If asked here:** *"Doesn't this just mean you wrote a normal program with an LLM sprinkled on it?"* — Honestly, a bit, and I'd take that as a compliment. The parts of this that are software are software because software is better at them. The model is there for the three things on the left, which no amount of Python was going to do.

---

## Slide 8 — Demo · 8 minutes

> Enough slides. I want you to make a decision.
>
> **Commands, in order**
>
> ```
> make report            # pick the naive run
> make report            # pick the recommended run
> make compare
> ```
>
> Everything is offline, replayed from frozen runs in `artifacts/runs/`. No model calls, no network, no keys. If the wifi dies this demo still works.
>
> **Beat 1 — open the naive memo.** [Open it. Scroll to the recommendations. Then stop talking for fifteen seconds and let them read.]
>
> > Read the strongest recommendation on that page. Same data, same question, same subagents, same tools as the version I'll show you second. The only difference is discipline — no memo-format skill telling it to cite sources and never round, no verifier, no runtime grading.
>
> **Beat 2 — ask the room.** 
>
> > You're the reviewer. Would you sign this? // What would you need? //
>
> [Let them find it. Somebody usually says "sources." Then land it:]
>
> > This memo makes about twenty claims about Apple's China antitrust exposure, EU DMA fines, and tariff guidance. It attributes **none** of them. And it quietly rounded six real tool outputs into tidier numbers — eighteen thousand four hundred and twenty-one instead of eighteen thousand four hundred twenty point five five. Nobody asked it to. It rounded because rounding reads better.
> >
> > And here's the part that should bother you: this is comfortably the **best-written** of the three memos. It's the only one that closes with a prioritised action plan. It is the one you would forward to a client. // The most persuasive memo was the least defensible one, and reading it carefully would not have told you that. That's not a model failure. That's a *category* failure — you cannot inspect grounding by reading.
>
> **Beat 3 — open the grounded memo.** [Click one number. Let the evidence panel open. Click a second one.]
>
> > Same question. Now every figure is a link. Click it and you get the tool call that produced it, the raw result, and which agent was holding it. This one is showing you `get_account_balances` — that's an MCP tool on the trading server, held by the portfolio analyst, and the ledger caught its output at the moment it arrived.
>
> **Beat 4 — `make compare`.**
>
> > Three configurations, recomputed from the frozen runs. Naive: ninety-two point four percent grounded, **zero** citations. Baseline — skills plus grounding rules, nothing checking them: ninety-nine point two, thirty-two citations. Verified — plus a verifier subagent and a runtime rubric loop: ninety-eight point six, forty-two citations.
> >
> > Notice the verified run scores *lower* on grounding than the baseline. That's not a regression. It made more claims and cited more of them, and the denominator moved. **This is why one number is not a verdict** — which is the whole back half of this session.
>
> **[If LangSmith is up]** Open the trace. First thing, before anything else: filter on `lc_agent_name`. A single run is forty-plus spans across four agents, and dropped in cold you will scroll and disengage. Collapse it to one subagent first. Navigation before content.

**Recovery plan, in order of preference:**
1. `make report` on the frozen runs — no network, no model.
2. `slides/trust-but-verify.html` — the checked-in workshop page.
3. Talk through the `make compare` table from this script. You know the numbers.

**Run `make doctor` before you present.** It checks keys, prompts, policy, and fixtures.

---

## Slide 9 — Failure story · 3.5 minutes

> I want to show you a bug I shipped while building this, because it is the exact thesis of the session and the system caught it on itself.
>
> That number — eighteen thousand four hundred twenty dollars and fifty-five cents — is the real cash balance. It came straight out of `get_account_balances`. It is as real as a number gets in this system. // And the verifier reported it as **unsupported**.
>
> Here's why. I installed `GroundingLedgerMiddleware` on the supervisor. Which felt right — one place, cross-cutting concern, that's what middleware is for. // But **declarative subagents don't inherit parent middleware.** When you define a subagent as configuration — a dict with a name, a prompt, and a tool list — the harness builds it for you, and it builds it with the middleware *it* was told about, not the middleware the parent happens to have.
>
> So every tool call made *inside* a subagent never reached the ledger. `get_account_balances` is wired into the **portfolio analyst**, and the portfolio analyst is a subagent. So the cash balance was fetched, used, and reported correctly in the memo — while the evidence for it was destroyed at the boundary between two context windows.
>
> Now sit with the failure *shape*, because the shape is the lesson. **Nothing errored.** No exception, no warning, no degraded-mode banner. The agent ran beautifully. It produced a good memo. The grounding score was seventy-six point six percent and I spent an embarrassingly long time trying to make the *model* cite better — because the score was pointing at the model and the bug was in my wiring.
>
> Fix was one line per subagent. Seventy-six point six to ninety-nine point two. The before-run is frozen in the repo as `artifacts/runs/ledger-bug`, so you can diff the two ledgers yourself.
>
> Two things to take from this. One, practical: **a cross-cutting control has to be installed per agent, and you should assert that it was.** There's now a test that walks the analyst subagents and fails if any of them is missing the ledger middleware — because "I remembered" is not a control. // And I'll be honest that the test only covers the analysts today; extending it to walk the whole registry is the obvious next commit, and it's exactly the kind of gap this session is about.
>
> Two, general: this is what a verification system is *for*. Not catching a model hallucinating — catching **you**. The most expensive bugs in agent systems are not wrong answers. They're controls that report success they did not achieve.

**If asked here:** *"How would we have caught this earlier?"* — The trace would have shown it in about thirty seconds. Filter to the spend analyst, look at its tool spans, and the ledger writes just aren't there. I didn't look, because the score looked like a model problem and I believed the score. That's the actual lesson.

---

## Slide 10 — Three tiers of evaluation · 4 minutes

> So how do you check a hundred and forty-two claims. Three tiers, and the order matters as much as the contents.
>
> **Tier one: deterministic.** Ordinary Python. Does every citation resolve to a source file that exists. Does every figure in the memo appear in the ledger, within a rounding tolerance. Free, instant, identical every time, runs in `pytest`. // Against twenty labelled defects, it catches **eighteen**. For zero dollars. That is the single best return in this entire architecture, and it's the tier teams skip because it doesn't feel like AI engineering.
>
> **Tier two: the LLM judge.** Reach for this *only* for the two it can't settle. And I want to be honest about both misses, because the honesty is the useful part.
>
> Miss one: a real 34.71% attributed to the wrong denominator. The number exists in the ledger, so the checker is satisfied — but it's a percentage of *equity* being presented as a percentage of the *portfolio*. That's a semantic error and code cannot see it.
>
> Miss two is worse, and it's a design trade with no clean answer. The checker has a rounding tolerance, because a memo that says "eighteen thousand four hundred twenty-one" for 18,420.55 is being helpful, not dishonest. That same tolerance lets a **fabricated 12%** through, because a real 11.88% rounds to 12. // Tighten the tolerance and honest reformatting starts failing your build. Loosen it and fabrication gets through. There is no setting that avoids both. I'd argue that trade-off is the most useful thing on this slide — you are not going to engineer your way out of it, you're going to *choose*, and you should choose knowing that's what you're doing.
>
> **Tier three: trajectory.** What the agent *did*, not what it said. Which tools it called, whether approvals happened, whether it stayed inside its budget. // This one protects against a specific and nasty failure: a memo that is perfectly grounded and completely vacuous, because the agent skipped an analysis and only made claims it could easily support. Grounding rewards caution. Trajectory checks are what stop caution from becoming emptiness.
>
> Bottom line: a right answer reached by an unsafe path is still a failed agent.

**If asked here — and you will be asked this:** *"Why not just write unit tests?"* — You should, and I did: the tier-one checks **are** unit tests. The distinction is between **invariants** and **distributions**. "This citation resolves" is an invariant — assert it, fail the build. "This memo is well-grounded" is a distribution — score it, watch it move, alert on the delta. Teams get stuck because they try to `assert` on a distribution, watch it flake, and conclude agents are untestable. They're not. You were just using the wrong instrument.

---

## Slide 11 — Evaluate the evaluator · 5 minutes

> This is the slide I'd keep if I could only keep one.
>
> There is an objection that decides whether any of this lands, and it usually arrives as a joke: *an LLM grading an LLM is turtles all the way down.* It's a good objection. I have two answers and the order matters.
>
> First answer: **don't use a judge where code will do.** That was the last slide — eighteen of twenty defects, for free. If you reach for a judge first, you deserve the turtles.
>
> Second answer: **the judge is measurable**, and if you don't measure it you're not doing evaluation, you're doing astrology with a dashboard.
>
> So. Twenty of my own labelled fixtures — twenty memo-and-claim pairs where I sat down and decided by hand whether each claim was supported. Same judge model throughout. Three versions of the judge *prompt*.
>
> **v1**, a reasonable first draft. Eighty-five percent agreement with my labels. It blessed **two** bad memos and flagged one good one.
>
> **v2.** I read v1's mistakes and wrote a stricter prompt. // Eighty-five percent. // Identical headline score.
>
> Now. If eighty-five to eighty-five is all you're looking at, what do you do? You throw the change away. You say "prompt engineering doesn't work, the numbers didn't move," and you go and look for a bigger model. //
>
> Look at the columns. v2 blessed **zero** bad memos. It eliminated the *entire* dangerous error class. What it did instead was flag three good memos — it got twitchy. And in this domain those two error classes have wildly different costs: a false accept is a wrong number in front of a client, and a false reject is a five-minute conversation with an engineer.
>
> **A single agreement number would have told you to throw away the change that mattered most.** That is the whole slide.
>
> v3 is calibrated — I kept v2's strictness and gave it explicit guidance on the class of thing it was over-flagging. Ninety-five percent, zero false accepts, one false reject.
>
> So the lesson is not "write a better prompt." The lesson is: **an evaluator is an application.** It has inputs, outputs, failure modes, and a version history. It needs its own labelled test set and its own error analysis, and you need that *before* you believe a single score it produces. // This is why so many teams have eval dashboards they quietly stopped looking at. Not because the dashboard is wrong — because nobody ever checked whether it was right, so nobody trusts it, so nobody acts on it.
>
> And building the labelled set is the step everybody skips because it feels like homework. Twenty examples, hand-labelled against one fixed evidence body — then pushed to a LangSmith dataset in about three lines with `create_dataset` and `create_examples`, so an experiment can run against them. **Twenty beats zero by an enormous margin**, and waiting until you can do two hundred properly is procrastination with better branding.

**If asked here:** *"Who decides the ground truth?"* — I did, and that's a real limitation, so say it plainly. In production this is an annotation queue with the people who actually own the risk — the compliance reviewers. What doesn't change is the mechanism: their labels become the dataset, agreement becomes the metric, and disagreements are the thing you read.

---

## Slide 12 — Enterprise controls · 3 minutes

> Quickly, because in a regulated room these questions arrive whether or not I put them on a slide.
>
> Tools come in over MCP — the Trading and Banking servers. Every one of them goes through a **blast-radius classifier** before the agent ever sees it. Verb rules, and it **fails closed**: if the classifier can't confidently place a tool, it's treated as dangerous. That default is the whole value of the thing.
>
> Three buckets. **Read** — visible to the model, no ceremony. **Write** — anything that changes account state. Hidden by default; and if you do enable it, it's wrapped in `interrupt_on`, which is a LangGraph interrupt: the run *pauses*, a human approves or rejects, and the run resumes from exactly where it stopped. **Secret** — anything that would put a credential in a context window. Never exposed at all, at any setting.
>
> The thing I want you to notice is that those are **two independent switches**, not one. Visibility is separate from execution. `ALLOW_WRITE_TOOLS` controls whether the model can even see a write tool. `interrupt_on` controls whether a visible one can fire without a person. Collapsing those into a single flag is how you end up with an agent that's one config change away from trading.
>
> And then the honest part. // This demo runs on synthetic data, an offline replay server, and `InMemorySaver` — an in-memory checkpointer. That is a *demo* choice and I'm not going to pretend otherwise. Production needs a durable checkpoint store and stable thread ids, so a crash resumes instead of restarting.
>
> One thing people get wrong about that: durable execution preserves *progress*. It does **not** make external actions exactly-once. If your agent placed a trade and then the process died, resuming will happily place it again. Side effects still need idempotency keys. That's your problem, not the framework's.
>
> Plus tenant isolation and an append-only audit log, which for this use case is the ledger you've already seen — it was designed for compliance, and observability was the bonus.

**If asked here — and this one usually arrives in the first ten minutes:** *"Our data can't leave the building."* — Three things, ninety seconds. Self-hosted LangSmith exists. Input and output masking exists, so you can trace structure without payloads. And this entire demo runs offline, which is a reasonable model for local development. Happy to go deeper after.

---

## Slide 13 — Close · 2 minutes

> Back to where we started. Compliance signs off Monday.
>
> The agent doesn't earn that signature because the prose is polished. It earns it because claims link to recorded evidence, risky actions cross an explicit boundary, and every evaluator in the path has been measured against human labels.
>
> If you forget this stack tomorrow, two practices survive.
>
> **One. Record evidence at the moment it arrives.** In middleware, not in your tools. Make the audit trail the default rather than an optional habit — because habits are things people forget, and forgetting doesn't throw.
>
> **Two. Measure the judge before you trust it.** A score is only useful once you understand the evaluator's own error distribution. Twenty labelled examples. Read the disagreements, not the average.
>
> Both of those transfer to whatever framework you're using, and both of them are why the customer's reviewer checks two flagged claims instead of re-deriving a hundred and forty-two. // That ratio is the product.
>
> Where this sits: agent fundamentals before, deployment and online evaluation after — the offline loop becomes continuous, with alerting and annotation queues and real user feedback. And the honest footnote is that verification roughly doubles tail latency, which deserves its own session rather than a bullet here.
>
> Thank you. What did I get wrong?

---

# Q&A — prepared answers

**Why Deep Agents rather than plain `create_agent` everywhere?**
For three of five agents, because the work is open-ended and context-heavy and I wanted the harness defaults — planning, subagents, filesystem, skills — rather than my own worse versions of them. For the other two I *did* use plain `create_agent`, because bounded work with one correct output shape doesn't need planning and doesn't benefit from autonomy. The interesting answer isn't "Deep Agents good," it's that the choice is per-agent.

**Why are the verifier and strategist shallow?**
Bounded input, exactly one right output shape. The strategist returns a validated `RecommendationSet` via `response_format`; its arithmetic comes from deterministic tools. The verifier runs a fixed checklist. In both cases a deep agent adds ways to deviate from a script I want followed exactly.

**What did this cost, and was it worth it?**
The first working version was 1.78 million tokens and 17.6 minutes per run. It's now 554 thousand tokens, 3.8 minutes, and 69 cents — at *higher* grounding. Four changes: prompt caching, model tiering (the researcher doesn't need the reasoning model), letting code render tables instead of the model, and running the free deterministic check *before* the paid one. My first attempt at that last one made things **40% worse**, because I added the cheap gate *alongside* the two LLM checks instead of *in front* of them. Whether the remaining cost is worth it depends on what happens at your company when a memo is wrong — that's a decision for the room, not for me.

**What else broke?**
Two more, same shape, both worth telling.
*A cost ceiling that truncated a run in silence.* I capped the supervisor at 30 model calls because a healthy run used about 30. A run hit exactly 30, `exit_behavior="end"` stopped it mid-sentence, and the CLI printed a grounding score and a report path as though nothing happened — the memo ended at an empty `## Portfolio` heading. The score was even *plausible*, because a truncated memo makes fewer claims and the ones it makes are the easy ones. Two rules came out of it: **a ceiling that binds during normal operation is set wrong** (mine are now ~3x observed usage), and **hitting one is a defect, not a degradation** — it's now reported above everything else.
*A verification loop that detected and never acted.* The gate ran in an `after_agent` hook and returned findings as a message, expecting a revision. `after_agent` runs when the agent has already finished — returning messages updates state and restarts nothing. The panel logged "2 to fix, revising" and the run ended with both fabricated quotes still in the memo. Detection worked, the fix never fired, and **the logs said otherwise**. A control that reports success it did not achieve is worse than no control, because it buys confidence instead of safety.

**One flagged figure was my fault, not the model's.**
`summarize_period` returned a percentage change but not the absolute change. The agent — quite reasonably wanting to report the dollar delta — computed it itself, and got flagged for it. The fix was to return the number, not to scold the model. **When verification flags a figure, ask first whether some tool should have returned it.**

**How would you extend this with online evaluation?**
Same evaluators, different trigger. Sample a percentage of production runs, score them asynchronously, alert on the delta rather than the absolute — because the absolute moves when the denominator moves, which you saw on the compare table. Route anything the judge flags into an annotation queue, and every human label that comes back becomes a new dataset example. That's the loop that keeps the judge calibrated as the domain drifts.

**What would you do differently if you started again?**
Install the ledger middleware from a registry with a test asserting coverage, from day one. And build the twenty labelled fixtures in week one instead of week three — I spent days tuning prompts against a score I had no reason to believe.

**Could a customer's team build this?**
The architecture, yes, in about a week. The part that takes longer is the labelled set and the argument with compliance about what "supported" means. That conversation is the actual project, and it's the part I'd want to be in the room for.
