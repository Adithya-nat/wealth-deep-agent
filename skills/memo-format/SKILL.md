---
name: memo-format
description: How to write the wealth memo so every claim in it can be mechanically verified. Read before writing or revising the memo.
---

# Memo format

The memo is checked by a program, not a person, before anyone reads it. The
rules below are not style preferences — they are what makes the check possible.

## Structure

The memo is a **decision document**, not a data dump. Someone should be able to
read it in three minutes and know what to do.

```markdown
# Wealth Review — <period>

## Recommended actions
The strategist's list, with its dollar amounts unchanged. For each: what to do,
how much, which policy rule triggered it, and any market context that bears on
it. If a recommendation is a HOLD, say why acting would be worse.

## Why now
Three to five bullets. Every external claim carries its source id.

## Portfolio
Prose only. Reference the tables by placeholder — see below.

{{table:drift}}

## Spending
Prose only. What changed, what is recurring, what is an artifact of a partial
month.

## What we could not verify
Anything you wanted to say but could not support. Do not omit this section.
An empty one reads "None." — a missing one reads as though you never checked.
```

## Tables: reference them, do not retype them

The report renderer builds tables from the recorded data. Write the placeholder
and it is expanded with the real numbers:

| Placeholder | What it renders |
|---|---|
| `{{table:holdings}}` | Every position: quantity, price, market value, P/L |
| `{{table:concentration}}` | Each position as a share of total portfolio value |
| `{{table:drift}}` | Current vs. target weight per sector, with breaches flagged |

Two reasons this matters. A table you retype costs output tokens every time you
revise the memo, and revision is the expensive part of a verified run. And a
table you retype **can be wrong** — a placeholder cannot be. Write the argument;
let the code write the evidence.

## Figures

**Every number must come from a tool result.** Not from memory, not from
arithmetic you did yourself, not from rounding a tool's answer to something
tidier.

- `$139,557.05` — a tool returned this. ✅
- `roughly $140K` — you rounded. The check will not find it. ❌
- `up about 12%` — no tool computed this. ❌
- `NVDA is 5.64% of the portfolio` — `concentration` returned it. ✅

If you need a figure no tool produces, delegate for it or say it is unavailable.

**Always state the denominator on a percentage.** `concentration` is a share of
total value *including cash*; `sector_exposure` is a share of *equity only*.
The same portfolio is 23% or 27% in the same fund depending on which you mean.

## Citations

Every claim about the outside world carries the source id the researcher
returned, in square brackets:

```markdown
NVIDIA reported "data center revenue growth of 41% year over year" [src_a91f0c2d].
```

Rules the checker enforces:

1. **The id must exist.** It has to be a source someone actually fetched. An id
   that looks plausible but was never fetched is the single worst failure mode
   here, because it is invisible to a reader.
2. **Quoted spans are matched literally** against the stored source text
   (whitespace and smart quotes normalized). If you are not reproducing the
   source word for word, do not use quotation marks — paraphrase and cite.
3. **One source id per claim.** Citing three sources for one sentence means no
   one can tell which supports it.

## Numbers that are not claims

Dates, section numbers, and small counts are ignored by the checker. You do not
need to source "three sectors" or "August 2026". You do need to source
"11 positions" if you state it as a finding — write it as a figure a tool
returned, or leave it out.

## Recommended actions

Every dollar amount in a recommendation comes from `rebalance_plan`. Copy it
exactly.

- `Trim NVDA by $2,410` — the plan returned 2410.00. ✅
- `Trim NVDA by about $2,400` — you rounded a trade instruction. ❌
- `Trim NVDA by $2,500` — you chose a tidier number. Nothing computed it. ❌

Name the policy rule behind each action, so the reader can disagree with the
rule rather than with you: "Information Technology is 34.71% of equity against
a 25% target with a 5-point band."

State what the tools could not see. No tool in this run returns tax lots, so
every recommendation to sell carries a tax consequence nobody computed. Say so.

## Before you finish

Ask for verification. Fix everything reported as `fabricated` — those are not
judgment calls. For anything reported as `unsupported`, either replace it with a
figure a tool returned or move it to "What we could not verify".
