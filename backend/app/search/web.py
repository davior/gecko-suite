"""Web search run by the suite, for any model.

Ported from gecko-notes' `web_search.py`, made synchronous and moved onto
`app.outbound`. Notes' reasoning holds unchanged: only Anthropic-protocol providers can
search server-side, so for every other model the search is run here and the hits are
handed back to the model as text. That makes search a capability of the assistant, not
of one vendor.

Four backends:
- **duckduckgo**: no account, no key. Scrapes the HTML endpoint the site's own no-JS form
  posts to, so it can be throttled; the error says so and points at the keyed backends.
- **brave**: Brave's Search API, with a free tier.
- **tavily**: built for LLMs; hits carry an extracted passage, not a one-line snippet.
- **searxng**: a SearXNG instance's JSON API, for people who run their own.

One change from Notes: redirects are not followed. Notes followed them, but a redirect to
another host would carry Brave's `X-Subscription-Token` with it (httpx strips only
`Authorization` across origins), and none of these endpoints redirect when used as
documented.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from lxml import html as lxml_html

from app import outbound

logger = logging.getLogger(__name__)

# One search should never hold a chat turn open for long: the model is waiting on it.
SEARCH_TIMEOUT = 20.0
DEFAULT_RESULTS = 5
MAX_RESULTS = 10
# Snippets go straight into a model's context, so cap what one hit can contribute.
MAX_SNIPPET_CHARS = 700

# DuckDuckGo's no-JS endpoint answers a bare client with an empty page. The same
# "look like a browser" posture as Notes' web_fetch, not an attempt to evade a block.
_SCRAPER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class KindSpec:
    label: str
    needs_api_key: bool
    needs_base_url: bool


KINDS: dict[str, KindSpec] = {
    "duckduckgo": KindSpec("DuckDuckGo", needs_api_key=False, needs_base_url=False),
    "brave": KindSpec("Brave Search API", needs_api_key=True, needs_base_url=False),
    "tavily": KindSpec("Tavily", needs_api_key=True, needs_base_url=False),
    "searxng": KindSpec("SearXNG", needs_api_key=False, needs_base_url=True),
}


class SearchError(Exception):
    """A search failed in a way worth reporting to the user verbatim."""

    def __init__(self, code: str, message: str, status_code: int = 502):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    published: Optional[str] = None


def _clean(text: Any) -> str:
    return " ".join(text.split()) if isinstance(text, str) else ""


def _truncate(text: str, limit: int = MAX_SNIPPET_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _result(title: Any, url: Any, snippet: Any, published: Any = None) -> Optional[SearchResult]:
    """Build a result, dropping anything without a usable http(s) URL."""
    url = _clean(url)
    if not url.startswith(("http://", "https://")):
        return None
    return SearchResult(title=_clean(title) or url, url=url, snippet=_truncate(_clean(snippet)),
                        published=_clean(published) or None)


def _http_error(label: str, status: int) -> SearchError:
    if status in (401, 403):
        return SearchError("search_auth_failed", f"{label} rejected the API key (HTTP {status}).")
    if status == 429:
        return SearchError("search_rate_limited", f"{label} is rate-limiting searches. Try again shortly.")
    return SearchError("search_failed", f"{label} returned HTTP {status}.")


def _json_body(response: httpx.Response, label: str) -> dict:
    try:
        payload = response.json()
    except ValueError:
        raise SearchError("search_failed", f"{label} returned a response that wasn't JSON.")
    if not isinstance(payload, dict):
        raise SearchError("search_failed", f"{label} returned an unexpected response shape.")
    return payload


def _collect(raw: Any, count: int, *, title: str, url: str, snippet: str, published: str) -> list[SearchResult]:
    results: list[SearchResult] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        hit = _result(item.get(title), item.get(url), item.get(snippet), item.get(published))
        if hit:
            results.append(hit)
        if len(results) >= count:
            break
    return results


# ─── backends ────────────────────────────────────────────────────────────────


def _duckduckgo(http: httpx.Client, query: str, count: int) -> list[SearchResult]:
    response = http.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query, "kl": "wt-wt"},
        headers={
            "User-Agent": _SCRAPER_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://html.duckduckgo.com/",
        },
    )
    if not response.is_success:
        raise _http_error("DuckDuckGo", response.status_code)
    results = parse_duckduckgo_html(response.text, count)
    if not results:
        # The scraper cannot tell "no hits" from "refused", so it says the likelier one.
        logger.info("duckduckgo returned no parseable results (%d bytes)", len(response.text))
        raise SearchError(
            "search_blocked",
            "DuckDuckGo returned no results; it throttles automated requests from server "
            "addresses. Try again, or set up Brave or Tavily in Gecko Suite.",
        )
    return results


def _unwrap_duckduckgo_link(href: str) -> str:
    """DuckDuckGo wraps every hit in a redirect (`/l/?uddg=<target>`); unwrap it."""
    href = (href or "").strip()
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if parsed.path.startswith("/l/") or "uddg" in (parsed.query or ""):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return target
    return href


def _has_class(token: str) -> str:
    # A plain contains(@class, 'result') would also match `results` and `result__body`,
    # so each token is matched with its surrounding spaces: XPath 1.0's one-class test.
    return f"contains(concat(' ', normalize-space(@class), ' '), ' {token} ')"


def parse_duckduckgo_html(body: str, count: int) -> list[SearchResult]:
    try:
        tree = lxml_html.fromstring(body)
    except Exception:
        return []
    results: list[SearchResult] = []
    seen: set[str] = set()
    for row in tree.xpath(f"//div[{_has_class('result')}]"):
        if "result--ad" in (row.get("class") or ""):
            continue  # sponsored rows are not search hits
        anchors = row.xpath(f".//a[{_has_class('result__a')}]")
        if not anchors:
            continue
        url = _unwrap_duckduckgo_link(anchors[0].get("href") or "")
        if not url or url in seen:
            continue
        snippets = row.xpath(f".//*[{_has_class('result__snippet')}]")
        hit = _result(anchors[0].text_content(), url, snippets[0].text_content() if snippets else "")
        if hit:
            seen.add(hit.url)
            results.append(hit)
        if len(results) >= count:
            break
    return results


def _brave(http: httpx.Client, query: str, count: int, api_key: str) -> list[SearchResult]:
    response = http.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": count, "safesearch": "moderate", "result_filter": "web"},
        headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
    )
    if not response.is_success:
        raise _http_error("The Brave Search API", response.status_code)
    raw = (_json_body(response, "Brave Search").get("web") or {}).get("results")
    return _collect(raw, count, title="title", url="url", snippet="description", published="page_age")


def _tavily(http: httpx.Client, query: str, count: int, api_key: str) -> list[SearchResult]:
    response = http.post(
        "https://api.tavily.com/search",
        json={"query": query, "max_results": count, "search_depth": "basic",
              "include_answer": False, "include_raw_content": False},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    if not response.is_success:
        raise _http_error("Tavily", response.status_code)
    raw = _json_body(response, "Tavily").get("results")
    return _collect(raw, count, title="title", url="url", snippet="content", published="published_date")


def _searxng(http: httpx.Client, query: str, count: int, base_url: str) -> list[SearchResult]:
    response = http.get(
        urljoin(base_url.rstrip("/") + "/", "search"),
        params={"q": query, "format": "json", "safesearch": 1, "language": "en"},
        headers={"Accept": "application/json", "User-Agent": _SCRAPER_UA},
    )
    if not response.is_success:
        if response.status_code == 403:
            # An instance without the JSON format enabled answers 403: configuration,
            # not a bad key.
            raise SearchError("search_forbidden", "The SearXNG instance refused the request. Its "
                              "settings.yml must list `json` under `search.formats`.")
        raise _http_error("The SearXNG instance", response.status_code)
    raw = _json_body(response, "SearXNG").get("results")
    return _collect(raw, count, title="title", url="url", snippet="content", published="publishedDate")


def search(*, kind: str, query: str, api_key: str = "", base_url: str = "",
           count: Optional[int] = None) -> list[SearchResult]:
    """Run one search. An empty list is a real answer for every backend but the scraper."""
    query = (query or "").strip()
    if not query:
        raise SearchError("empty_query", "The search query was empty.", 400)
    spec = KINDS.get(kind)
    if spec is None:
        raise SearchError("unknown_provider", f"Unknown web search backend: {kind}", 400)
    if spec.needs_api_key and not api_key:
        raise SearchError("search_unconfigured", f"{spec.label} needs an API key.", 409)
    if spec.needs_base_url and not base_url:
        raise SearchError("search_unconfigured", f"{spec.label} needs an instance URL.", 409)

    count = max(1, min(int(count or DEFAULT_RESULTS), MAX_RESULTS))
    timeout = httpx.Timeout(SEARCH_TIMEOUT, connect=10.0, write=10.0, pool=10.0)
    try:
        with outbound.client(timeout=timeout) as http:
            if kind == "duckduckgo":
                return _duckduckgo(http, query, count)
            if kind == "brave":
                return _brave(http, query, count, api_key)
            if kind == "tavily":
                return _tavily(http, query, count, api_key)
            return _searxng(http, query, count, base_url)
    except httpx.TimeoutException:
        raise SearchError("search_timeout", f"{spec.label} did not respond within {SEARCH_TIMEOUT:.0f}s.", 504)
    except httpx.RequestError as exc:
        raise SearchError("search_unreachable", f"Could not reach {spec.label}: {type(exc).__name__}")


# ─── image search ────────────────────────────────────────────────────────────
#
# Pictures for an app to offer a person to choose from (GVC's backgrounds and overlays).
# The same backend a person set up for text search answers, so there is one place to
# configure search, not two. What comes back is a list of candidates and nothing more:
# the suite never downloads a picture, since it keeps no app content. The app that wants
# one has GAM fetch it.

DEFAULT_IMAGES = 12
MAX_IMAGES = 30
MAX_IMAGE_DESCRIPTION_CHARS = 300


@dataclass
class ImageResult:
    image_url: str
    # The page the picture is on, which is what a credit should name. Tavily does not say
    # which page an image came from, so it can be missing: never filled with a guess.
    page_url: Optional[str] = None
    title: str = ""
    thumbnail_url: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    description: str = ""


def _http_url(value: Any) -> Optional[str]:
    value = _clean(value)
    return value if value.startswith(("http://", "https://")) else None


def _int(value: Any) -> Optional[int]:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _image(image_url: Any, *, page_url: Any = None, title: Any = "", thumbnail_url: Any = None,
           width: Any = None, height: Any = None, description: Any = "") -> Optional[ImageResult]:
    url = _http_url(image_url)
    if url is None:
        return None
    return ImageResult(image_url=url, page_url=_http_url(page_url), title=_truncate(_clean(title), 200),
                       thumbnail_url=_http_url(thumbnail_url), width=_int(width), height=_int(height),
                       description=_truncate(_clean(description), MAX_IMAGE_DESCRIPTION_CHARS))


def _dedupe(images: list[Optional[ImageResult]], count: int) -> list[ImageResult]:
    # Engines list one picture under several pages; one candidate per file is enough.
    seen: set[str] = set()
    kept: list[ImageResult] = []
    for image in images:
        if image is None or image.image_url in seen:
            continue
        seen.add(image.image_url)
        kept.append(image)
        if len(kept) >= count:
            break
    return kept


def _tavily_images(http: httpx.Client, query: str, count: int, api_key: str) -> list[ImageResult]:
    # Tavily has no image endpoint: its search returns pictures beside the pages. Asking
    # for descriptions is what makes them choosable by a model that cannot see them.
    response = http.post(
        "https://api.tavily.com/search",
        json={"query": query, "max_results": 5, "search_depth": "basic", "include_answer": False,
              "include_raw_content": False, "include_images": True, "include_image_descriptions": True},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    if not response.is_success:
        raise _http_error("Tavily", response.status_code)
    images = []
    for item in _json_body(response, "Tavily").get("images") or []:
        if isinstance(item, str):  # the shape without descriptions
            images.append(_image(item))
        elif isinstance(item, dict):
            images.append(_image(item.get("url"), description=item.get("description")))
    return _dedupe(images, count)


def _brave_images(http: httpx.Client, query: str, count: int, api_key: str) -> list[ImageResult]:
    response = http.get(
        "https://api.search.brave.com/res/v1/images/search",
        # Brave's image search takes only "off" or "strict".
        params={"q": query, "count": count, "safesearch": "strict"},
        headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
    )
    if not response.is_success:
        raise _http_error("The Brave Search API", response.status_code)
    images = []
    for item in _json_body(response, "Brave Search").get("results") or []:
        if not isinstance(item, dict):
            continue
        properties = item.get("properties") or {}
        images.append(_image(properties.get("url"), page_url=item.get("url"), title=item.get("title"),
                             thumbnail_url=(item.get("thumbnail") or {}).get("src"),
                             width=properties.get("width"), height=properties.get("height")))
    return _dedupe(images, count)


def _searxng_images(http: httpx.Client, query: str, count: int, base_url: str) -> list[ImageResult]:
    response = http.get(
        urljoin(base_url.rstrip("/") + "/", "search"),
        params={"q": query, "format": "json", "safesearch": 1, "categories": "images"},
        headers={"Accept": "application/json", "User-Agent": _SCRAPER_UA},
    )
    if not response.is_success:
        if response.status_code == 403:
            raise SearchError("search_forbidden", "The SearXNG instance refused the request. Its "
                              "settings.yml must list `json` under `search.formats`.")
        raise _http_error("The SearXNG instance", response.status_code)
    images = []
    for item in _json_body(response, "SearXNG").get("results") or []:
        if not isinstance(item, dict):
            continue
        width = height = None
        # SearXNG reports size as text, "1920 x 1080" or "1920×1080", when it knows it.
        size = _clean(item.get("resolution")).replace("×", "x").split("x")
        if len(size) == 2:
            width, height = size[0].strip(), size[1].strip()
        images.append(_image(item.get("img_src"), page_url=item.get("url"), title=item.get("title"),
                             thumbnail_url=item.get("thumbnail_src"), width=width, height=height,
                             description=item.get("content")))
    return _dedupe(images, count)


def search_images(*, kind: str, query: str, api_key: str = "", base_url: str = "",
                  count: Optional[int] = None) -> list[ImageResult]:
    """Find pictures with one backend. An empty list is a real answer."""
    query = (query or "").strip()
    if not query:
        raise SearchError("empty_query", "The search query was empty.", 400)
    spec = KINDS.get(kind)
    if spec is None:
        raise SearchError("unknown_provider", f"Unknown web search backend: {kind}", 400)
    if kind == "duckduckgo":
        # Its image results come from a JavaScript endpoint behind a per-query token
        # scraped out of a page, which is a scraper's scraper: it would break unannounced
        # and look like "no pictures" when it did. Saying so is more useful.
        raise SearchError("images_unsupported", "DuckDuckGo, the search used when none is set up, "
                          "can't find pictures. Set up Tavily, Brave or SearXNG in Gecko Suite's "
                          "search settings.", 409)
    if spec.needs_api_key and not api_key:
        raise SearchError("search_unconfigured", f"{spec.label} needs an API key.", 409)
    if spec.needs_base_url and not base_url:
        raise SearchError("search_unconfigured", f"{spec.label} needs an instance URL.", 409)

    count = max(1, min(int(count or DEFAULT_IMAGES), MAX_IMAGES))
    timeout = httpx.Timeout(SEARCH_TIMEOUT, connect=10.0, write=10.0, pool=10.0)
    try:
        with outbound.client(timeout=timeout) as http:
            if kind == "brave":
                return _brave_images(http, query, count, api_key)
            if kind == "tavily":
                return _tavily_images(http, query, count, api_key)
            return _searxng_images(http, query, count, base_url)
    except httpx.TimeoutException:
        raise SearchError("search_timeout", f"{spec.label} did not respond within {SEARCH_TIMEOUT:.0f}s.", 504)
    except httpx.RequestError as exc:
        raise SearchError("search_unreachable", f"Could not reach {spec.label}: {type(exc).__name__}")
