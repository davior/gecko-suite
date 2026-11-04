"""Which URL a provider is actually addressed at, and over which protocol.

Ported from GVC (from GAM, from gecko-notes' `routers/settings.py`), whose reasoning is
the part worth carrying: three of the decisions here look arbitrary and are not.

The Messages protocol is no longer only Anthropic's. DeepSeek publishes an
Anthropic-compatible endpoint at api.deepseek.com/anthropic, and that endpoint runs the
same SERVER-SIDE web_search tool Claude does, so a DeepSeek provider pointed at it
searches the web natively, with no third-party search key. Its OpenAI-compatible
endpoint has no such tool.

A provider opts in with `use_anthropic_api`; `anthropic` itself is always on this path.
Everything downstream keys off `speaks_anthropic()`, never off the vendor name.
"""

from __future__ import annotations

from typing import Optional

from app.models.ai import AIProvider
from app.vocab import (
    ANTHROPIC_CAPABLE_VENDORS,
    VENDOR_ANTHROPIC,
    VENDOR_DEEPSEEK,
    VENDOR_OLLAMA,
)

ANTHROPIC_BASE = "https://api.anthropic.com"
DEEPSEEK_ANTHROPIC_BASE = "https://api.deepseek.com/anthropic"
OPENAI_BASE = "https://api.openai.com"
DEEPSEEK_BASE = "https://api.deepseek.com"
OLLAMA_BASE = "http://localhost:11434"


def speaks_anthropic(provider: AIProvider) -> bool:
    """Whether this provider is addressed over the Anthropic Messages protocol."""
    if provider.vendor == VENDOR_ANTHROPIC:
        return True
    return bool(provider.use_anthropic_api) and provider.vendor in ANTHROPIC_CAPABLE_VENDORS


def anthropic_base(provider: AIProvider) -> str:
    """Base URL for a provider's Messages endpoint.

    Anthropic's own is fixed, and so is DeepSeek's: its stored `base_url` would describe
    the OpenAI-compatible endpoint, so it is ignored here. A fixed managed endpoint spares
    the user a field and stops a crafted base_url redirecting the request. Any other
    provider opting in supplies its own gateway URL, SSRF-checked when it was saved.
    """
    if provider.vendor == VENDOR_ANTHROPIC:
        return ANTHROPIC_BASE
    if provider.vendor == VENDOR_DEEPSEEK:
        return DEEPSEEK_ANTHROPIC_BASE
    return (provider.base_url or ANTHROPIC_BASE).rstrip("/")


def openai_compat_base(vendor: Optional[str], base_url: Optional[str]) -> str:
    """Base URL for an OpenAI-compatible provider.

    DeepSeek is a fixed managed endpoint, like OpenAI's own, so it ignores any stored
    base_url. `openai` and `custom` supply their own, SSRF-checked on save. Ollama's /v1
    endpoint speaks the same protocol, tools included, at its own (possibly private)
    address.
    """
    if vendor == VENDOR_DEEPSEEK:
        return DEEPSEEK_BASE
    if vendor == VENDOR_OLLAMA:
        return (base_url or OLLAMA_BASE).rstrip("/")
    return (base_url or OPENAI_BASE).rstrip("/")


def anthropic_headers(api_key: str, vendor: str, *, web_search: bool = False) -> dict[str, str]:
    """Auth and protocol headers for one Messages request.

    Beta flags go only to Anthropic: they name Anthropic-internal features, and a gateway
    that does not recognise one could reject the whole request. Compatible gateways get
    BOTH auth headers because they disagree about which to read (DeepSeek documents
    `x-api-key`, other gateways read a bearer token), and an unread header is ignored.
    """
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    if vendor == VENDOR_ANTHROPIC:
        headers["anthropic-beta"] = "pdfs-2024-09-25"
        if web_search:
            # Only for a request that carries the server-side search tool (the raw
            # passthrough sees the caller's own tools; the normalized path builds them).
            headers["anthropic-beta"] += ",web-search-2025-03-05"
    else:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers
