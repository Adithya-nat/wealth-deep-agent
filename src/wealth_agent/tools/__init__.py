"""Tools the subagents call.

Split by which subagent owns them, because tool ownership *is* the context
boundary: the spend analyst never sees a quote, the portfolio analyst never
sees a transaction, and neither of them can fetch a web page. Narrow tool sets
are the cheapest context engineering available.
"""

from wealth_agent.tools.portfolio import build_portfolio_tools
from wealth_agent.tools.research import build_research_tools
from wealth_agent.tools.spend import build_spend_tools

__all__ = ["build_portfolio_tools", "build_research_tools", "build_spend_tools"]
