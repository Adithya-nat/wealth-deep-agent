"""Where the numbers come from, and where they are written down.

`store` holds the run workspace and the append-only grounding ledger that every
verification claim is checked against. `adapters` joins the three live Robinhood
calls a position actually needs into the one shape the analytics layer expects,
so the arithmetic lives in tested Python rather than in a model's head.
`synthetic` generates the deterministic fixtures; `redact` is for the rare case
where a real capture has to be shown.
"""
