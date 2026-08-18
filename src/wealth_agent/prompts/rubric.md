---
name: rubric
description: The criteria the runtime grading loop scores a memo against.
---
- Every figure in the memo traces to a recorded tool result (verify_report reports zero `unsupported` figures).
- Every citation resolves to a source that was actually fetched, and every quoted span appears in the source it is attributed to (zero `fabricated` findings).
- The overall grounding score from verify_report is at least 0.95.
- Every percentage states its denominator.
- Every recommended action carries a dollar amount that appears in a recorded `rebalance_plan` result.
- The memo covers portfolio allocation, spending, recommended actions, and at least one externally sourced piece of market context.
