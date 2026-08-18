"""The typed shape of what the agent recommends.

Structured output is doing real work here rather than being a nicety. The
recommendations are the part of the memo a human acts on with money, and three
things follow from making them a schema instead of prose:

* **The report renders them without parsing anything.** No regex over model
  prose to find a dollar amount, which is a class of bug that only shows up on
  the sentence you did not anticipate.
* **The fields you must not omit cannot be omitted.** `reason_code` and
  `rationale` are required, so a recommendation that does not say which policy
  rule triggered it fails validation instead of reaching a client.
* **The human-approval gate has something to show.** "Approve this?" needs a
  list of trades, not a paragraph to re-read under time pressure.

What is deliberately *not* enforced here is that `dollars` matches a recorded
`rebalance_plan` result. Pydantic cannot know that. It is checked downstream by
the same grounding machinery that checks every other figure in the memo — which
is the point of having built that machinery in the first place.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Action = Literal["TRIM", "BUY", "HOLD"]

ReasonCode = Literal[
    "SECTOR_OVER_BAND",
    "SECTOR_UNDER_BAND",
    "SINGLE_NAME_OVER_CAP",
    "BELOW_MIN_TRADE",
    "CASH_RESERVE_SHORT",
]


class Recommendation(BaseModel):
    """One thing to do, or one thing deliberately not done."""

    action: Action = Field(description="TRIM, BUY, or HOLD.")
    symbol: str | None = Field(
        default=None,
        description="Ticker. Null only when the action is about a sector with no holding.",
    )
    dollars: float = Field(
        description=(
            "The amount, copied exactly from a rebalance_plan result. Zero for HOLD. "
            "Never rounded, adjusted, or netted against another row."
        )
    )
    reason_code: ReasonCode = Field(
        description="The policy rule that triggered this, from the rebalance plan."
    )
    rationale: str = Field(
        description=(
            "Two or three sentences: the drift in the policy's own terms, any market "
            "context with its source id, and anything the tools could not see."
        )
    )
    policy_conflict: str | None = Field(
        default=None,
        description="Set when two policy rules disagree about this position.",
    )


class RecommendationSet(BaseModel):
    """Everything the strategist decided, ranked."""

    summary: str = Field(
        description=(
            "Two or three sentences a person could read on their phone and understand "
            "what changed and what to do."
        )
    )
    actions: list[Recommendation] = Field(
        default_factory=list,
        description="At most five, most severe breach first.",
    )
    unaddressed: list[str] = Field(
        default_factory=list,
        description=(
            "Drifts, risks, or questions the tools surfaced but this set does not act "
            "on, and why. An empty list is a claim that nothing was left out."
        ),
    )

    @property
    def trades(self) -> list[Recommendation]:
        """Only the actions that would place an order."""
        return [a for a in self.actions if a.action in ("TRIM", "BUY") and a.dollars > 0]

    @property
    def net_dollars(self) -> float:
        """Signed total: negative means the plan raises cash on balance."""
        return round(
            sum(a.dollars if a.action == "BUY" else -a.dollars for a in self.trades), 2
        )


__all__ = ["Action", "Recommendation", "RecommendationSet", "ReasonCode"]
