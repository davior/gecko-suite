"""The closed vocabularies the AI registry is built from.

Vendors, capabilities, apps and tasks are allowlists: a value outside them is a 400 on
write rather than a row nothing can interpret later. Model ids are deliberately *not*
here. They change faster than this service is deployed, so they are catalogue rows
(`CatalogEntry`) and free text on `AIModel`, never code.
"""

from __future__ import annotations

# ─── scopes ──────────────────────────────────────────────────────────────────
# An instance row is shared with everybody and only an administrator writes it. A user
# row belongs to one person and wins over the instance row for them (D3).
SCOPE_INSTANCE = "instance"
SCOPE_USER = "user"
SCOPES = (SCOPE_INSTANCE, SCOPE_USER)

# ─── vendors ─────────────────────────────────────────────────────────────────
VENDOR_ANTHROPIC = "anthropic"
VENDOR_OPENAI = "openai"
VENDOR_DEEPSEEK = "deepseek"
VENDOR_OLLAMA = "ollama"
VENDOR_CUSTOM = "custom"
# Registered from M1 so the importer can bring their keys across; called from M3.
VENDOR_FAL = "fal"
VENDOR_DEEPGRAM = "deepgram"

VENDORS = (
    VENDOR_ANTHROPIC,
    VENDOR_OPENAI,
    VENDOR_DEEPSEEK,
    VENDOR_OLLAMA,
    VENDOR_CUSTOM,
    VENDOR_FAL,
    VENDOR_DEEPGRAM,
)

# Vendors that serve chat and embeddings over an LLM protocol. fal and Deepgram have
# their own APIs and are reached only through the media and speech endpoints.
LLM_VENDORS = (VENDOR_ANTHROPIC, VENDOR_OPENAI, VENDOR_DEEPSEEK, VENDOR_OLLAMA, VENDOR_CUSTOM)

# Vendors that may be pointed at an Anthropic-compatible endpoint instead of their own.
# `anthropic` is always on that path. `ollama` is excluded deliberately: its base URL is
# the one address allowed to be private, so honouring the flag there would aim a
# Messages request at an internal host. (Carried over from Notes, GAM and GVC.)
ANTHROPIC_CAPABLE_VENDORS = (VENDOR_DEEPSEEK, VENDOR_CUSTOM)

# Vendors whose base_url is user-supplied and therefore SSRF-checked before it is
# stored. anthropic, deepseek, fal and deepgram resolve to fixed endpoints and never read
# it; ollama is the deliberate private-address exception.
CHECKED_BASE_URL_VENDORS = (VENDOR_OPENAI, VENDOR_CUSTOM)

# Vendors that need no API key. Ollama runs on the user's own machine.
KEYLESS_VENDORS = (VENDOR_OLLAMA,)

# ─── capabilities ────────────────────────────────────────────────────────────
CAPABILITIES = (
    "chat",
    "vision",
    "tools",
    "thinking",
    "web_search",  # a server-side search tool the model runs itself
    "embed",
    "image_gen",
    "image_edit",
    "video_gen",
    "tts",
    "stt",
)

# ─── apps ────────────────────────────────────────────────────────────────────
APPS = ("notes", "gam", "gvc")

# ─── tasks ───────────────────────────────────────────────────────────────────
# What a model is asked to do, which is what routing is keyed on: "the assistant uses a
# capable model, autotagging a cheap one" is a statement about tasks, not about apps.
TASKS = (
    "chat.assistant",
    "chat.draft",
    "chat.edit",
    "chat.quick",
    "enrich.describe",
    "enrich.summarize",
    "enrich.autotag",
    "embed.text",
    "image.generate",
    "image.edit",
    "video.generate",
    "speech.tts",
    "speech.stt",
)

# What a model must be able to do to be assigned a task. Checked when the assignment
# is written, so a misrouted task is a 400 on the settings screen, not a vendor error on
# somebody's first request.
TASK_REQUIRES: dict[str, frozenset[str]] = {
    "chat.assistant": frozenset({"chat"}),
    "chat.draft": frozenset({"chat"}),
    "chat.edit": frozenset({"chat"}),
    "chat.quick": frozenset({"chat"}),
    "enrich.describe": frozenset({"chat", "vision"}),
    "enrich.summarize": frozenset({"chat"}),
    "enrich.autotag": frozenset({"chat"}),
    "embed.text": frozenset({"embed"}),
    "image.generate": frozenset({"image_gen"}),
    "image.edit": frozenset({"image_edit"}),
    "video.generate": frozenset({"video_gen"}),
    "speech.tts": frozenset({"tts"}),
    "speech.stt": frozenset({"stt"}),
}

# Where a task with no assignment of its own falls back to, so one assignment
# ("chat.assistant: Claude Opus") is enough to make every chat-shaped task work, and the
# finer ones exist only for people who want them. One level, never a chain to search:
# routing should be explainable in a sentence.
TASK_FALLBACK: dict[str, str] = {
    "chat.draft": "chat.assistant",
    "chat.edit": "chat.assistant",
    "chat.quick": "chat.assistant",
    "enrich.describe": "chat.assistant",
    "enrich.summarize": "chat.assistant",
    "enrich.autotag": "chat.assistant",
    "image.edit": "image.generate",
}
