"""Which provider of a vendor a person's request goes through.

For vendors an app chooses by name rather than by routed model (fal's endpoints, Deepgram's
models): the person's own provider, else the shared one. A provider with no usable key is
skipped, not an error, so someone with a broken personal key still reaches the shared one.
"""

from __future__ import annotations

from typing import Optional

from sqlmodel import Session, col

from app import crypto
from app.models.ai import AIProvider
from app.services.registry import visible_providers_query
from app.vocab import SCOPE_USER


def find_provider(session: Session, user_id: str, vendor: str) -> Optional[AIProvider]:
    rows = session.exec(
        visible_providers_query(user_id)
        .where(AIProvider.vendor == vendor, col(AIProvider.enabled).is_(True))
        .order_by(col(AIProvider.created_at))).all()
    rows = [r for r in rows if crypto.decrypt(r.api_key)]
    # Personal first: it is what the person chose for themselves.
    rows.sort(key=lambda r: 0 if r.scope == SCOPE_USER else 1)
    return rows[0] if rows else None
