"""DuckDuckGo web search tool (works with any model, including Azure)."""

from __future__ import annotations

from typing import Any

from pydantic_ai import Agent, RunContext

from claw.memory import ClawDeps


def duckduckgo_search(query: str, *, max_results: int = 5) -> str:
    """Run a DuckDuckGo text search and format results for the model."""
    q = (query or "").strip()
    if not q:
        return "error: query is required"
    limit = max(1, min(int(max_results), 10))
    try:
        from ddgs import DDGS
    except ImportError as exc:
        return f"error: ddgs package not installed ({exc})"

    try:
        with DDGS() as ddgs:
            rows: list[dict[str, Any]] = list(ddgs.text(q, max_results=limit))
    except Exception as exc:  # noqa: BLE001
        return f"error: DuckDuckGo search failed: {type(exc).__name__}: {exc}"

    if not rows:
        return f"No results for {q!r}."

    lines = [f"DuckDuckGo results for {q!r}:"]
    for i, row in enumerate(rows, 1):
        title = str(row.get("title") or "").strip()
        href = str(row.get("href") or row.get("link") or "").strip()
        body = str(row.get("body") or row.get("snippet") or "").strip()
        lines.append(f"{i}. {title}")
        if href:
            lines.append(f"   {href}")
        if body:
            lines.append(f"   {body}")
    return "\n".join(lines)


def register_web_search_tool(agent: Agent[ClawDeps, str]) -> None:
    @agent.tool
    def web_search(
        ctx: RunContext[ClawDeps],
        query: str,
        max_results: int = 5,
    ) -> str:
        """Search the web via DuckDuckGo for current information.

        Use for weather, news, docs, facts that may change, or anything not in memory.
        Returns titles, URLs, and snippets.
        """
        _ = ctx
        return duckduckgo_search(query, max_results=max_results)
