"""Web research for the `market-researcher` subagent.

The important line in this module is the one that writes the fetched page to
disk before returning anything to the model.

A normal search tool hands the model a wall of page text. The model reads it,
summarizes it, and the original is gone — so when the memo later says a source
claimed something, there is nothing left to check it against. Every citation
becomes an act of faith.

Here, :func:`fetch_page` persists the full text under a content-addressed id and
returns a *preview* plus that id. Three things follow:

* The model cites `[src_a91f0c2d]`, and the id resolves to a file.
* The full text never enters a context window, so a 40kB page costs ~200 tokens.
* :mod:`wealth_agent.verify` can check a quoted span character-by-character
  against what was actually fetched.

Offloading for context and offloading for verifiability turn out to be the same
move. That is the nicest thing about this design and worth saying out loud.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from langchain_core.tools import BaseTool, tool
from markdownify import markdownify

from wealth_agent.data.store import RunWorkspace

#: How much of a fetched page the model sees inline. Enough to judge relevance
#: and locate a quote; not enough to tempt it into summarizing from the preview.
PREVIEW_CHARS = 1200

#: Cap on stored page text. Beyond this, pages are almost always navigation.
MAX_SOURCE_CHARS = 60_000

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 wealth-agent/0.1"


def _tavily_client() -> Any:
    from tavily import TavilyClient

    key = os.getenv("TAVILY_API_KEY")
    if not key:
        msg = "TAVILY_API_KEY is not set; the market-researcher cannot search."
        raise RuntimeError(msg)
    return TavilyClient(api_key=key)


def build_research_tools(ws: RunWorkspace) -> list[BaseTool]:
    """Build the research toolset bound to one run's source store."""

    @tool
    def web_search(query: str, max_results: int = 5) -> dict[str, Any]:
        """Search the web for pages relevant to a query.

        Returns titles, URLs, and short snippets only. Nothing is citable until
        you call `fetch_page` on it — a search snippet is the search engine's
        summary, not the source's words.

        Args:
            query: What to search for.
            max_results: How many results to return.
        """
        response = _tavily_client().search(
            query=query, max_results=max_results, search_depth="advanced"
        )
        return {
            "query": query,
            "results": [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": (r.get("content", "") or "")[:300],
                }
                for r in response.get("results", [])
            ],
        }

    @tool
    def fetch_page(url: str) -> dict[str, Any]:
        """Fetch a page, store its full text, and return a citable source id.

        Cite the returned `source_id` in the memo as `[src_xxxxxxxx]`. Quote
        only spans that appear in `preview`, or read the stored file for more.

        Args:
            url: Absolute URL to fetch.
        """
        try:
            response = httpx.get(
                url, timeout=20.0, follow_redirects=True, headers={"User-Agent": _UA}
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # Returned rather than raised: a dead link is information the agent
            # should route around, not a reason to fail the whole run.
            return {"url": url, "error": f"fetch failed: {exc}"}

        text = markdownify(response.text, heading_style="ATX").strip()
        text = "\n".join(line for line in text.splitlines() if line.strip())[
            :MAX_SOURCE_CHARS
        ]
        title = _title_of(text) or url
        source = ws.write_source(url=url, title=title, text=text)
        return {
            "source_id": source.id,
            "url": url,
            "title": title,
            "chars_stored": len(text),
            "preview": text[:PREVIEW_CHARS],
            "cite_as": f"[{source.id}]",
        }

    @tool
    def read_source(source_id: str, offset: int = 0, limit: int = 4000) -> dict[str, Any]:
        """Read more of an already-fetched source.

        Args:
            source_id: An id returned by `fetch_page`.
            offset: Character offset to start from.
            limit: How many characters to return.
        """
        source = ws.sources().get(source_id)
        if source is None:
            return {"error": f"{source_id} has not been fetched"}
        chunk = source.text[offset : offset + limit]
        return {
            "source_id": source_id,
            "url": source.url,
            "offset": offset,
            "returned_chars": len(chunk),
            "total_chars": len(source.text),
            "text": chunk,
        }

    @tool
    def list_sources() -> dict[str, Any]:
        """List every source fetched so far in this run, with its citable id."""
        return {
            "sources": [
                {"source_id": s.id, "url": s.url, "title": s.title, "chars": len(s.text)}
                for s in ws.sources().values()
            ]
        }

    return [web_search, fetch_page, read_source, list_sources]


def _title_of(markdown: str) -> str | None:
    for line in markdown.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


__all__ = ["MAX_SOURCE_CHARS", "PREVIEW_CHARS", "build_research_tools"]
