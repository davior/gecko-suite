import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.clock import utcnow


def new_id() -> str:
    return str(uuid.uuid4())


class User(SQLModel, table=True):
    """A shadow of a gecko-notes account, until identity moves here (M6).

    The same row GAM and GVC keep. Notes signs a token, the suite verifies it, and this
    row is upserted from the token's claims, so providers, routing and usage have
    something local to hang ownership off.

    `id` is the `sub` claim, not generated here, and must stay equal to the id Notes
    assigned. That is also what makes M6 possible without touching the other apps: when
    the suite takes over sign-in, it imports Notes' accounts *into these rows* with their
    ids unchanged, so every `user_id` column in every app in the suite stays valid.
    """

    id: str = Field(primary_key=True)
    username: str = Field(default="", index=True)
    email: Optional[str] = None
    is_admin: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow)
    last_seen: Optional[datetime] = None
