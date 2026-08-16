---
name: category-rulebook
description: How card descriptors become merchants and categories, and what to do with the ones that do not match. Read when spending categories look wrong or a large share of charges are Uncategorized.
---

# Category rulebook

`load_spend_data` normalizes every transaction before you see it. You do not
need to categorize anything by hand — but you do need to know how it works, so
you can tell a real finding from an artifact of the rules.

## The pipeline

Three tiers, cheapest first:

1. **Clean the descriptor.** Strip processor prefixes (`SQ *`, `TST*`,
   `PAYPAL *`), store numbers (`#4821`), and city/state tails. Pure string work.
2. **Match the rulebook.** An ordered list of regex → (merchant, category).
   First match wins, so specific rules precede general ones.
3. **Fall through.** No match means merchant = cleaned descriptor, category =
   `Uncategorized`.

The rulebook is matched against the **raw** descriptor, not the cleaned one —
cleaning can remove the token that identifies the merchant. `AMZN.COM/BILL`
looks like a URL suffix to the cleaner and like Amazon to the rulebook.

## What to do about `Uncategorized`

`load_spend_data` reports the count. Treat it as a data-quality signal:

- **Under ~5% of charges** — normal long tail. Mention it, move on.
- **Over ~15%** — the rulebook is stale relative to this person's spending, and
  every category total is understated by an unknown amount. Say so explicitly
  in the memo rather than reporting the totals as though they were complete.

Use `spending_by_merchant(category="Uncategorized")` to see what is falling
through. If a handful of merchants account for most of it, name them — that is
the actionable version of "5% uncategorized".

## Rules the rulebook cannot settle

Some descriptors are genuinely ambiguous. `APPLE.COM/BILL` covers both an
iCloud subscription and a laptop. A regex cannot tell them apart, and neither
can you without the amount and cadence.

When it matters, reason about it explicitly and say what you assumed. Do not
silently pick one — an assumption stated in the memo can be corrected by the
reader; an assumption buried in a total cannot.

## What is not spend

`Payment` rows (statement autopay) and `refund` rows are excluded from every
spend total by the tools. This is the most common way spend numbers come out
wrong elsewhere: summing an `amount` column that mixes charges with payments
gives a number that means nothing.

If asked about total outflow *including* card payments, say plainly that the
tools measure charges, and that counting both would double-count — the payment
settles the charges already counted.

## Recurring detection

`find_recurring_charges` groups by merchant and requires:

- at least `min_occurrences` **distinct months** (default 3), and
- a coefficient of variation (stdev / mean) below **0.15**.

The CV threshold is what separates a subscription from a merchant you happen to
visit monthly. A coffee shop charged 14 times a month at varying amounts has a
high CV and is correctly not a subscription. A gym at exactly $265 every month
has a CV near zero.

Annual cost is `typical_amount × 12`. That is an extrapolation, not an
observation — when you report it, say "on current pricing".
