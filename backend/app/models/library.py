"""What the apps' assistants share besides models: prompts, recipes, personas, web
search, and the credentials the apps themselves use to call the suite.

Prompts, recipes, personas and search providers follow the registry's scope rule: an
instance row is everybody's and only an admin writes it; a user row is one person's. An
`app` column narrows a row to one app; null means every app.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.clock import utcnow


def new_id() -> str:
    return str(uuid.uuid4())


class SystemPrompt(SQLModel, table=True):
    """From Notes, where each user has a list and one is active.

    Here "active" is `is_default`, per (scope, owner, app): the prompt an app uses when
    the user has not picked one. One default per combination is enforced by the router.
    """

    __tablename__ = "system_prompt"

    id: str = Field(default_factory=new_id, primary_key=True)
    scope: str = Field(index=True)
    user_id: Optional[str] = Field(default=None, index=True)
    app: Optional[str] = None
    name: str
    content: str
    is_default: bool = Field(default=False)
    sort_order: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Recipe(SQLModel, table=True):
    """A saved prompt a user runs by name ("Summarise for a newsletter"). From Notes."""

    __tablename__ = "recipe"

    id: str = Field(default_factory=new_id, primary_key=True)
    scope: str = Field(index=True)
    user_id: Optional[str] = Field(default=None, index=True)
    app: Optional[str] = None
    name: str
    prompt: str
    # JSON-as-TEXT list of strings.
    tags: str = "[]"
    sort_order: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Persona(SQLModel, table=True):
    """An assistant with a name, a voice and, optionally, its own models.

    A chat request that names a persona gets its system prompt first (the most stable
    block, so it stays in the cached prefix) and its model for the task, if it has one,
    instead of the user's routing.
    """

    __tablename__ = "persona"

    id: str = Field(default_factory=new_id, primary_key=True)
    scope: str = Field(index=True)
    user_id: Optional[str] = Field(default=None, index=True)
    app: Optional[str] = None
    name: str
    description: str = ""
    # A URL or a path under data/ once avatar upload exists (with the frontend).
    avatar: Optional[str] = None
    system_prompt: str = ""
    # JSON-as-TEXT {task: AIModel.id}. Checked against the caller's visibility on every
    # use, not only when saved: a shared persona may name a model one user cannot see.
    model_overrides: Optional[str] = None
    sort_order: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class WebSearchProvider(SQLModel, table=True):
    """A search backend: duckduckgo, brave, tavily or searxng. From Notes, where it was
    one per user in settings; here a user's own beats the shared one."""

    __tablename__ = "web_search_provider"

    id: str = Field(default_factory=new_id, primary_key=True)
    scope: str = Field(index=True)
    user_id: Optional[str] = Field(default=None, index=True)
    name: str
    kind: str
    api_key: str = ""  # encrypted with app.crypto
    base_url: Optional[str] = None  # SearXNG only; SSRF-checked on save
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class AppClient(SQLModel, table=True):
    """The credential an app uses to call the suite on a user's behalf without that
    user's token: a GAM enrichment job that outlived it, a GVC render at 3am.

    One per app. The secret is shown once when it is issued and stored only as a hash.
    A SHA-256 is enough here, without a slow KDF: the secret is 32 random bytes, so there
    is no dictionary to try it against.
    """

    __tablename__ = "app_client"

    app: str = Field(primary_key=True)
    secret_hash: str
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utcnow)
    last_used_at: Optional[datetime] = None
