"""Themes, and each person's preferences (M7).

A theme is Notes' shape, ported: light or dark, a flat colour, a gradient or an image
behind everything, and how the glass panels and their shadows look. The four apps share
one stylesheet (`glass.css`), so the same values render the same everywhere.

Themes follow the registry's scope rule: an instance theme is everybody's and only an
admin writes it; a user theme is one person's. Which theme an app wears is a preference,
chosen per app (`theme.notes`, `theme.gam`, ...), so Notes can be dark while GAM is not.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from app.clock import utcnow


def new_id() -> str:
    return str(uuid.uuid4())


class Theme(SQLModel, table=True):
    __tablename__ = "theme"

    id: str = Field(default_factory=new_id, primary_key=True)
    scope: str = Field(index=True)
    user_id: Optional[str] = Field(default=None, index=True)
    name: str
    mode: str = "light"  # light | dark
    bg_type: str = "flat"  # flat | gradient | image
    bg_color1: str = "#f0f4ff"
    bg_color2: Optional[str] = None
    # Absolute: the page that draws it may be any of the four apps.
    bg_image_url: Optional[str] = None
    bg_image_mode: str = "fill"  # fill | stretch | repeat
    bg_blur: float = 0.0
    glass_opacity: float = 0.30
    glass_blur: float = 12.0
    shadow_size: float = 8.0
    shadow_blur: float = 16.0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class UserPreference(SQLModel, table=True):
    """One preference for one person: `key` names it, `value` is JSON-as-text.

    A key-value table so that the next shared preference is a new key, not a migration.
    """

    __tablename__ = "user_preference"
    __table_args__ = (UniqueConstraint("user_id", "key"),)

    id: str = Field(default_factory=new_id, primary_key=True)
    user_id: str = Field(index=True)
    key: str = Field(index=True)
    value: str = "null"
    updated_at: datetime = Field(default_factory=utcnow)
