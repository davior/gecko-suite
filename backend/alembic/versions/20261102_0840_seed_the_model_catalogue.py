"""Seed the model catalogue with known models and list prices.

Prices are USD per million tokens and are *list* prices: every cost the suite derives
from them is stored as an estimate (`cost_estimated`). A key that is absent from a
row's pricing is unknown, never free.

Sources, recorded per row in `note`:
- Anthropic: the Claude API reference's current-models table (cached 2026-10-06), which
  agrees with GVC's `usage/pricing.py`. Cache-read prices are stored explicitly where
  the reference states them: they differ per model and are not a fixed multiple.
- OpenAI and DeepSeek: carried over from the pricing tables Notes, GAM and GVC share.
  Not re-verified here; check them against the vendors' price pages.
- Embedding models: no price recorded anywhere in the suite, so none is invented.

Family rows end in "*" and price a model id the exact rows do not know, the way the
siblings' substring catch-alls did. They assume the current generation's price.

To change a price later, write a new migration that updates rows by (vendor, model_id).
Never edit this one: databases that already ran it would keep the old figure.

Revision ID: 5b1f0c2a9d47
Revises: 7e488e3cc0c2
Create Date: 2026-11-02 08:40:00+00:00
"""
import json
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "5b1f0c2a9d47"
down_revision: Union[str, None] = "7e488e3cc0c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CLAUDE_REF = "Claude API reference, current models (2026-10-06)"
SIBLINGS = "Carried over from the Notes/GAM/GVC pricing tables; not re-verified"

CLAUDE_FULL = ["chat", "vision", "tools", "thinking", "web_search"]
CLAUDE_NO_SEARCH = ["chat", "vision", "tools", "thinking"]


def _price(inp, out, cache_read=None):
    price = {"currency": "USD", "input": inp, "output": out}
    if cache_read is not None:
        price["cache_read"] = cache_read
    return price


# (vendor, model_id, label, capabilities, max_tokens, context_window, embed_dims, pricing, note)
ROWS = [
    # ─── Anthropic ───────────────────────────────────────────────────────────
    ("anthropic", "claude-fable-5-1", "Claude Fable 5.1", CLAUDE_FULL, 128_000, 1_000_000, None,
     _price(10.0, 50.0, 0.25), CLAUDE_REF),
    ("anthropic", "claude-opus-5-5", "Claude Opus 5.5", CLAUDE_FULL, 128_000, 1_000_000, None,
     _price(4.0, 20.0, 0.20), CLAUDE_REF),
    ("anthropic", "claude-sonnet-5-5", "Claude Sonnet 5.5", CLAUDE_FULL, 128_000, 1_000_000, None,
     _price(2.0, 10.0, 0.20), CLAUDE_REF),
    ("anthropic", "claude-haiku-5-5", "Claude Haiku 5.5", CLAUDE_NO_SEARCH, 128_000, 1_000_000, None,
     _price(0.10, 0.50),
     CLAUDE_REF + ". The price is for prompts up to 100K tokens; $0.50/$2.50 beyond"),
    ("anthropic", "claude-fable-5", "Claude Fable 5", CLAUDE_FULL, 128_000, 1_000_000, None,
     _price(10.0, 50.0), CLAUDE_REF),
    ("anthropic", "claude-opus-5", "Claude Opus 5", CLAUDE_FULL, 128_000, 1_000_000, None,
     _price(5.0, 25.0), CLAUDE_REF),
    ("anthropic", "claude-opus-4-8", "Claude Opus 4.8", CLAUDE_FULL, 128_000, 1_000_000, None,
     _price(5.0, 25.0), CLAUDE_REF),
    ("anthropic", "claude-sonnet-5", "Claude Sonnet 5", CLAUDE_FULL, 128_000, 1_000_000, None,
     _price(2.0, 10.0), CLAUDE_REF),
    ("anthropic", "claude-sonnet-4-6", "Claude Sonnet 4.6", CLAUDE_FULL, 128_000, 1_000_000, None,
     _price(3.0, 15.0), CLAUDE_REF),
    ("anthropic", "claude-haiku-4-5", "Claude Haiku 4.5", CLAUDE_NO_SEARCH, None, 200_000, None,
     _price(1.0, 5.0), CLAUDE_REF),
    # Older generations priced unlike their family's current tier.
    ("anthropic", "claude-opus-4-1*", "Claude Opus 4.1", [], None, None, None,
     _price(15.0, 75.0), SIBLINGS),
    # Families: an id none of the rows above knows is priced as its current tier.
    ("anthropic", "claude-fable-*", "", [], None, None, None, _price(10.0, 50.0), CLAUDE_REF),
    ("anthropic", "claude-mythos-*", "", [], None, None, None, _price(10.0, 50.0), CLAUDE_REF),
    ("anthropic", "claude-opus-*", "", [], None, None, None, _price(4.0, 20.0), CLAUDE_REF),
    ("anthropic", "claude-sonnet-*", "", [], None, None, None, _price(2.0, 10.0), CLAUDE_REF),
    ("anthropic", "claude-haiku-*", "", [], None, None, None, _price(0.10, 0.50), CLAUDE_REF),
    ("anthropic", "claude-*", "", [], None, None, None, _price(4.0, 20.0),
     CLAUDE_REF + "; an unknown Claude model is assumed to be at the current Opus tier"),
    # ─── OpenAI ──────────────────────────────────────────────────────────────
    ("openai", "gpt-4.1", "GPT-4.1", ["chat", "vision", "tools"], None, None, None,
     _price(2.0, 8.0), SIBLINGS),
    ("openai", "gpt-4.1-mini", "GPT-4.1 mini", ["chat", "vision", "tools"], None, None, None,
     _price(0.40, 1.60), SIBLINGS),
    ("openai", "gpt-4.1-nano", "GPT-4.1 nano", ["chat", "vision", "tools"], None, None, None,
     _price(0.10, 0.40), SIBLINGS),
    ("openai", "gpt-4o", "GPT-4o", ["chat", "vision", "tools"], None, None, None,
     _price(2.50, 10.0), SIBLINGS),
    ("openai", "gpt-4o-mini", "GPT-4o mini", ["chat", "vision", "tools"], None, None, None,
     _price(0.15, 0.60), SIBLINGS),
    ("openai", "o3", "o3", ["chat", "vision", "tools", "thinking"], None, None, None,
     _price(2.0, 8.0), SIBLINGS),
    ("openai", "o4-mini", "o4-mini", ["chat", "vision", "tools", "thinking"], None, None, None,
     _price(1.10, 4.40), SIBLINGS),
    ("openai", "gpt-*", "", [], None, None, None, _price(2.50, 10.0),
     SIBLINGS + "; an unknown GPT model is assumed to be at the 4o tier"),
    ("openai", "text-embedding-3-small", "text-embedding-3-small", ["embed"], None, None, 1536,
     None, "GAM's default embedding model (it requests 512 dimensions); no price recorded"),
    ("openai", "text-embedding-3-large", "text-embedding-3-large", ["embed"], None, None, 3072,
     None, "No price recorded"),
    # ─── DeepSeek ────────────────────────────────────────────────────────────
    ("deepseek", "deepseek-chat", "DeepSeek Chat", ["chat", "tools"], None, None, None,
     _price(0.27, 1.10), SIBLINGS + ". Cache-miss price; DeepSeek's cache-hit discount is not modelled"),
    ("deepseek", "deepseek-reasoner", "DeepSeek Reasoner", ["chat", "thinking"], None, None, None,
     _price(0.55, 2.19), SIBLINGS + ". Cache-miss price"),
    ("deepseek", "deepseek-*", "", [], None, None, None, _price(0.27, 1.10), SIBLINGS),
    # ─── Ollama (always free: priced at zero by vendor, not by row) ──────────
    ("ollama", "nomic-embed-text", "nomic-embed-text", ["embed"], None, None, 768, None,
     "GAM's default local embedding model"),
]

catalog = sa.table(
    "catalog_entry",
    sa.column("id", sa.String),
    sa.column("vendor", sa.String),
    sa.column("model_id", sa.String),
    sa.column("label", sa.String),
    sa.column("capabilities", sa.String),
    sa.column("max_tokens", sa.Integer),
    sa.column("context_window", sa.Integer),
    sa.column("embed_dims", sa.Integer),
    sa.column("pricing", sa.String),
    sa.column("note", sa.String),
    sa.column("sort_order", sa.Integer),
)


def upgrade() -> None:
    op.bulk_insert(
        catalog,
        [
            {
                "id": str(uuid.uuid4()),
                "vendor": vendor,
                "model_id": model_id,
                "label": label,
                "capabilities": json.dumps(sorted(caps)),
                "max_tokens": max_tokens,
                "context_window": context,
                "embed_dims": dims,
                "pricing": json.dumps(pricing, sort_keys=True) if pricing else None,
                "note": note,
                "sort_order": index,
            }
            for index, (vendor, model_id, label, caps, max_tokens, context, dims, pricing, note)
            in enumerate(ROWS)
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM catalog_entry")
