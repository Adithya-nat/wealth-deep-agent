---
name: verification-protocol
description: How to read a verification report and what to do about each verdict. Read when verify_report returns failures.
---

# Verification protocol

`verify_report` runs two deterministic checks over the memo and returns a
verdict per claim. No model is involved, so its findings are facts about the
recorded evidence, not opinions about the memo.

## The three verdicts

| Verdict | Means | What to do |
|---|---|---|
| `grounded` | Traces to a recorded tool result or fetched source. | Nothing. |
| `unsupported` | No recorded evidence supports it. **It may still be true.** | Replace with a figure a tool returned, or move the claim to "What we could not verify". |
| `fabricated` | Cites a source that was never fetched, or quotes a span that is not in the source it names. | Remove or re-attribute. Never negotiate with this one. |

The distinction between `unsupported` and `fabricated` is the whole point of the
taxonomy. "I cannot find this" and "this contradicts what we recorded" call for
different responses, and collapsing them into a single "fail" throws away the
signal that tells you which.

## Fixing `fabricated` citations

Two causes, and they need different fixes:

**The source id does not exist.** Almost always attribution drift: a subagent
read six sources, compressed them into a summary, and the id got attached to
the wrong claim on the way back. Call `list_sources`, find which source actually
supports the claim, re-attribute. If none does, delete the claim.

**The quoted span is not in the source.** You paraphrased inside quotation
marks. Either reproduce the source's words exactly, or drop the quotation marks
and cite the paraphrase.

## Fixing `unsupported` figures

Ask where the number was supposed to come from:

- **A tool computed something close.** You rounded or reformatted past the
  tolerance. Use the tool's figure verbatim.
- **You did the arithmetic.** Delegate it. If no tool computes it, it does not
  go in the memo as a number.
- **It came from a source you did not fetch.** Fetch it, or drop the claim.

## The threshold

A memo passes at a grounding score of **0.95 or above with zero fabrications**.

Not 1.0. Perfect grounding on a memo of any length usually means the memo is
saying nothing — every hedge removed, every interesting comparison dropped. The
threshold is deliberately set where a careful memo can reach it and a careless
one cannot.

Zero fabrications is not negotiable, at any score. A single invented citation
does more damage to trust than ten unsupported figures, because a reader who
spot-checks one citation and finds it hollow will disbelieve the entire memo —
correctly.
