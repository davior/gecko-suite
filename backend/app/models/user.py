import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.clock import utcnow


def new_id() -> str:
    return str(uuid.uuid4())


class User(SQLModel, table=True):
    """An account. Until M6 a shadow of a gecko-notes account; from M6 the account itself.

    `id` is the token's `sub`, never generated for an imported account, and must stay equal
    to the id Notes assigned: every `user_id` column in every app in the suite refers to it.
    The importer copies Notes' accounts *into these rows*, ids unchanged (decision D7).

    A row with no `hashed_password` is a shadow (someone seen only through a token) and
    cannot sign in here. Uniqueness of `username` and `email` is enforced in code, not by
    the database: shadow rows written before M6 may share an empty username.
    """

    id: str = Field(primary_key=True)
    username: str = Field(default="", index=True)
    email: Optional[str] = Field(default=None, index=True)
    is_admin: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow)
    last_seen: Optional[datetime] = None
    # Accounts (M6). Ported from gecko-notes' User.
    hashed_password: Optional[str] = None
    is_active: bool = Field(default=True)
    email_verified: bool = Field(default=False)
    # None (off), "email" (a code by email at sign-in) or "totp" (an authenticator app).
    two_factor_method: Optional[str] = None
    # Encrypted with app.crypto (SUITE_ENCRYPTION_KEYS), not the JWT-derived key Notes used.
    totp_secret: Optional[str] = None
    last_login: Optional[datetime] = None


class AuthToken(SQLModel, table=True):
    """A single-use secret for an out-of-band step: an email-verification link, a
    password-reset link, or an emailed sign-in code. Only its SHA-256 is stored, and it
    is spent by setting `used_at`. Ported from gecko-notes."""

    __tablename__ = "auth_token"

    id: str = Field(default_factory=new_id, primary_key=True)
    user_id: str = Field(index=True)
    purpose: str = Field(index=True)  # verify_email | password_reset | twofa_email
    token_hash: str = Field(index=True)
    expires_at: datetime
    used_at: Optional[datetime] = None
    # Wrong codes tried against an emailed sign-in code: capped, so six digits cannot be
    # walked through.
    attempts: int = Field(default=0)
    created_at: datetime = Field(default_factory=utcnow)


class InstanceSetting(SQLModel, table=True):
    """An instance-wide switch an administrator sets on the Users screen (whether anyone
    may sign up, whether a new address must be verified). JSON-as-TEXT."""

    __tablename__ = "instance_setting"

    key: str = Field(primary_key=True)
    value: str = "null"
    updated_at: datetime = Field(default_factory=utcnow)
