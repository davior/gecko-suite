"""Password hashing: bcrypt, readable from gecko-notes' hashes as they stand.

Notes hashed with passlib's bcrypt, which writes standard `$2b$` hashes; `bcrypt` itself
reads them, so an imported account signs in here with the password it had. passlib is not
used: it is unmaintained and only wraps this.

bcrypt reads at most 72 bytes of a password. passlib cut longer ones silently; the bcrypt
library refuses them in newer releases. Cutting here, before both hashing and checking,
keeps a long password that worked in Notes working here.
"""

from __future__ import annotations

import bcrypt

MIN_PASSWORD_LENGTH = 8
_BCRYPT_MAX_BYTES = 72


def _bytes(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_bytes(password), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, hashed: str | None) -> bool:
    """False, never an error, for a missing or malformed hash: a shadow account (no
    password) simply cannot sign in."""
    if not password or not hashed:
        return False
    try:
        return bcrypt.checkpw(_bytes(password), hashed.encode("ascii"))
    except ValueError:
        return False


def check_strength(password: str) -> str:
    """Notes' policy, unchanged: at least eight characters. Returns the password, for use
    inside a pydantic validator."""
    if password is None or len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    return password
