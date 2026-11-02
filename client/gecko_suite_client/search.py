"""Web search through the suite: the user's own backend, else the shared one, else
DuckDuckGo. For models with no built-in search, an app offers this as a tool."""

from __future__ import annotations

from typing import Optional

from gecko_suite_client.client import SuiteClient

# What an app can pass straight to run_agent's `tools` to give any model web search.
WEB_SEARCH_TOOL = {
    "name": "web_search",
    "description": "Search the web. Returns titles, links and short passages.",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
}


def web_search(client: SuiteClient, query: str, *, count: Optional[int] = None, app: Optional[str] = None,
               subject_ref: Optional[str] = None) -> list[dict]:
    body: dict = {"query": query}
    for key, value in (("count", count), ("app", app or client.app), ("subject_ref", subject_ref)):
        if value:
            body[key] = value
    return client.request("POST", "/api/v1/search/web", json=body)["results"]


def format_results(results: list[dict]) -> str:
    """Hits as text a model reads well, numbered so it can cite them."""
    if not results:
        return "No results."
    return "\n\n".join(f"[{i}] {r['title']}\n{r['url']}\n{r.get('snippet', '')}".strip()
                       for i, r in enumerate(results, 1))
