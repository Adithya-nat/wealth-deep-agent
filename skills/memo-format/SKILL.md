---
name: memo-format
description: How to write the wealth memo so every claim in it can be mechanically verified. Read before writing or revising the memo.
---

# Memo format

The memo is checked by a program, not a person, before anyone reads it. The
rules below are not style preferences — they are what makes the check possible.

## Structure

```markdown
# Wealth Review — <period>

## Summary
Three to five sentences. What changed, what matters, what to do.

## Portfolio
Allocation, concentration, unrealized P/L.

## Spending
Totals by category, notable merchants, recurring commitments, trend.

## Market context
External information relevant to the holdings above. Every claim cited.

## What we could not verify
Anything you wanted to say but could not support. Do not omit this section.
An empty one reads "None." — a missing one reads as though you never checked.
```

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

## Before you finish

Ask for verification. Fix everything reported as `fabricated` — those are not
judgment calls. For anything reported as `unsupported`, either replace it with a
figure a tool returned or move it to "What we could not verify".
