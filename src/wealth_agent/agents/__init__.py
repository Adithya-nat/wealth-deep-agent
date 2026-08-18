"""One file per agent, so the directory listing is the architecture.

    supervisor              deep     plans, delegates, writes the memo
    portfolio_analyst       deep     holdings, allocation, concentration, P/L
    spend_analyst           deep     card feed, categories, trends
    market_researcher       deep     search, fetch, cite
    allocation_strategist   shallow  policy -> drift -> typed recommendations
    verifier                shallow  two deterministic checks, nothing else

A subagent is a context window with a job, and the harness follows from the job.
The two shallow agents are the interesting ones: both have exactly one right
answer to reach, so both are plain `create_agent` ReAct loops rather than a
fourth and fifth `create_deep_agent`.
"""
