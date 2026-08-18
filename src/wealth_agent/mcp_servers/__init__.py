"""Talking to Robinhood's MCP servers — and to the local stand-in for them.

`auth` runs OAuth 2.1 with dynamic client registration and PKCE from a headless
process. `clients` partitions the tool surface by blast radius and fails closed
on tools it does not recognise. `replay_server` is a real MCP server over
fixtures, not a mock, so demo mode exercises the same code path as live.
"""
