"""Cross-cutting concerns, installed per agent.

Declarative subagents are compiled with their own middleware stack and inherit
nothing from the parent, so every entry here has to be attached to each agent
that needs it. Getting that wrong is silent — see `grounding_ledger`.
"""

from wealth_agent.middleware.grounding_ledger import GroundingLedgerMiddleware

__all__ = ["GroundingLedgerMiddleware"]
