# Working in this repository

This file is loaded two ways: by coding agents working *on* this repo, and by
the wealth agent itself as deep-agent memory (`memory=["/AGENTS.md"]`). Keep it
short enough that it earns its place in every context window.

## Reference discipline (read this first)

Two MCP servers are connected. They answer different questions, and using the
wrong one wastes a turn:

| Server | Use it for | Example |
|---|---|---|
| **`docs-langchain`** | Concepts, guides, how-to, "does the framework support X", anything narrative. | "How do deep agent skills load?" |
| **`reference-langchain`** | Exact signatures, parameter names and types, return types, symbol lookup. | "What are the arguments to `RubricMiddleware`?" |

**Never write a LangChain, LangGraph, or deepagents API call from memory.**
Look up the signature in `reference-langchain` first. This stack moves fast
enough that a plausible-looking call is usually a version-old call, and the
resulting error surfaces at runtime rather than at import.

Search the docs before searching the web. The docs MCP is authoritative and
current; a blog post is neither.

## Architecture in one paragraph

A supervisor deep agent delegates to three deep subagents (`portfolio-analyst`,
`spend-analyst`, `market-researcher`) and one deliberately shallow one
(`verifier`, a plain `create_agent` ReAct loop). Data reaches the agent through
two Robinhood MCP servers, replayed from fixtures in demo mode. Everything a
tool returns is recorded to an append-only ledger by middleware, which is what
makes the memo's claims checkable afterwards. See `src/wealth_agent/supervisor.py`.

## Conventions

- **Deterministic code computes; the model decides.** Any figure that reaches
  the memo must come out of a function, not a model. If you are adding a
  capability that produces a number, it is a tool, not a prompt instruction.
- **New tools need no ledger code.** `GroundingLedgerMiddleware` records every
  tool result. Do not add recording calls inside tools.
- **Write tools are gated.** Anything that changes account state must be
  classified as a write in `mcp_clients.py` and interrupted for human approval.
  When in doubt, the classifier fails closed — leave it that way.
- **Never commit real account data.** `DEMO_MODE=1` is the default. Raw captures
  go to `captures/` (gitignored); only scrubbed fixtures belong in `artifacts/`.

## Running things

```bash
uv sync
uv run wealth demo            # the workshop walkthrough, offline
uv run wealth run "..."       # one memo, demo data
uv run pytest                 # unit tests + the eval regression gate
```
