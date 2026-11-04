"""The session token, the two-factor challenge, and single-use secrets.

The session token is gecko-notes' token: HS256 under `JWT_SECRET_KEY`, `sub` the account
id, 30 days. Two claims are added, `email` and `is_admin`, so an app that has never seen
someone (Notes after the cutover) can make its local record of them from the token alone.
Apps that read only `sub` and `username` are unaffected.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from jose import JWTError, jwt

from app.clock import utcnow
from app.config import settings

TWO_FACTOR_CHALLENGE_MINUTES = 5


def _encode(claims: dict, lifetime: timedelta) -> str:
    return jwt.encode({**claims, "exp": utcnow() + lifetime}, settings.jwt_secret_key,
                      algorithm=settings.jwt_algorithm)


def session_token(user) -> str:
    return _encode({"sub": user.id, "username": user.username, "email": user.email or "",
                    "is_admin": bool(user.is_admin)}, timedelta(days=settings.access_token_days))


def challenge_token(user_id: str, method: str) -> str:
    """Proof that the password step passed, good only for the second step and only for
    five minutes. Notes' shape, so a challenge from either issuer reads the same."""
    return _encode({"sub": user_id, "twofa_pending": True, "method": method},
                   timedelta(minutes=TWO_FACTOR_CHALLENGE_MINUTES))


def read_challenge(token: str) -> dict:
    """The challenge's claims, or JWTError. A session token is refused here: it must not
    stand in for the second factor."""
    claims = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    if not claims.get("twofa_pending"):
        raise JWTError("Not a two-factor challenge")
    return claims


def url_token() -> str:
    return secrets.token_urlsafe(32)


def numeric_code(digits: int = 6) -> str:
    return f"{secrets.randbelow(10 ** digits):0{digits}d}"


def hash_secret(raw: str) -> str:
    """Links and codes are stored as their SHA-256, never as they were sent."""
    return hashlib.sha256(raw.strip().encode()).hexdigest()
