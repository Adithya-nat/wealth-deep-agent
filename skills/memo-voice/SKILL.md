---
name: memo-voice
description: How the humanizer skill applies to a financial memo, and the three places this domain overrides it. Read with humanizer, before writing or revising the memo.
---

# Memo voice

Read `/skills/humanizer/SKILL.md` first. It has the 35 patterns and worked
examples. This file is only the part that changes because the document is a
financial memo that a program checks before a person reads it.

## Which parts of humanizer matter most here

A memo that reads as machine-generated gets skimmed and discounted however well
grounded it is. In practice these are the ones that show up in this document:

- **§5 vague sources.** "Analysts expect", "industry reports suggest",
  "market observers note". This is the citation rule wearing different clothes:
  name the source and cite its id, or cut the sentence.
- **§1 inflated claims** and **§4 sales language.** No "pivotal", "crucial",
  "robust", "strong performance". A number is either large enough to matter or
  it is not. Give the number and let the reader judge.
- **§8 avoiding is and are.** "VOO is 23.36% of the portfolio", not "VOO
  represents a position that stands as".
- **§17 sentence case headings.** "Recommended actions", not "Recommended
  Actions".
- **§23 filler** and **§24 stacked qualifiers.** "may potentially" is one hedge
  too many.
- **§25 generic positive endings.** End on the last concrete fact, not on
  "well positioned for continued growth".
- **§29 heading repetition.** Under "Spending", do not open with "Spending in
  this period...".

## Three overrides

**1. Grounding beats prose, always.** Never drop a figure, a source id, a
denominator, or an entry in "What we could not verify" to improve a sentence.
If a humanizer rule would cost you one of those, the rule loses. Sounding human
is worth nothing if the memo stops being checkable.

**2. Naming a limit is a fact, not a hedge.** §21 warns about knowledge-limit
disclaimers and speculative gap-filling — and its own corrected example is
"the founding date is not documented in the available sources", which is
exactly the form this memo needs. "No tool in this run returns tax lots, so the
tax cost is not estimated" is a specific, checkable statement about the run.
Keep it. §24 is about padding, not about honesty.

**3. Keep one name per thing.** §11 says do not cycle synonyms, and here that
is stricter than style: the position is "VOO" every time. "The fund", "the
ETF", "the holding" make a document that a program has to check ambiguous.

## Two smaller notes

**Bold marks the action, not emphasis.** §15 and §16 warn about decorative bold
and bold-label lists. `**Trim AAPL by $11,759.48.**` opens a recommendation
because it *is* the instruction. Do not bold ordinary words, tickers, or
figures inside prose.

**Dashes (§14) are banned here, and one kind of dash is worse than style.**

There is no writer sample for this memo, so §14 applies exactly as written:
**no em dashes (—) and no en dashes (–) in the finished memo.** Use a comma, a
colon, a full stop, or parentheses. This is the rule most often ignored on a
first draft, so check for the characters before you finish.

Two exceptions, both narrow:

- A numeric range keeps its en dash: `10–12%`, `$4,456.99–$4,803.03`.
- A minus sign is a **hyphen**, never a dash: write `-$1,621.90`. The grounding
  checker reads a dash before a figure as a negative sign, and it only tolerates
  that at all because someone fixed it after it silently flagged three true
  claims as unsupported.

You can also write "UNH is down $1,621.90" with no sign at all — the portfolio
tool returns the absolute value alongside the signed one precisely so that
ordinary English grounds.

## Do not smooth away tension

If two policy rules disagree — a single-name cap against a sector target — say
so and leave it unresolved. A memo that reads as though everything reconciled
neatly, when it did not, is worse writing *and* worse analysis. §34 warns
against answering objections nobody raised; it does not ask you to hide a
conflict the tools actually found.
