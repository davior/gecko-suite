"""Encrypting provider keys and other secrets at rest.

The suite is where every API key in the Gecko Suite ends up, so this is deliberately not
the scheme the sibling apps use:

- **Its own key, not one derived from JWT_SECRET_KEY.** Notes, GAM and GVC hash the JWT
  secret into a Fernet key. That makes one secret to manage, and it also means rotating
  the session secret silently makes every stored key unreadable, and leaking it leaks
  the keys too. Here the two are independent.
- **MultiFernet, so keys rotate.** The first key in `SUITE_ENCRYPTION_KEYS` encrypts and
  every key decrypts. Rotation is: put a new key first, run `reencrypt` over every stored
  value, then remove the old key.
- **No plaintext fallback.** The siblings return a value without the `enc:` prefix as-is,
  for databases from before they encrypted. Nothing is written here unencrypted, so a
  value without the prefix is corruption, and it reads as unset rather than as a key.

Legacy values carried over from the sibling apps by the importer (M1) are decrypted with
`decrypt_legacy`, which reproduces their JWT-derived key, and are then re-encrypted with
`encrypt` on the way in.
"""

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.config import settings

logger = logging.getLogger(__name__)

PREFIX = "enc:"


def _multi() -> MultiFernet:
    # Built per call rather than cached at import, so a test (or a rotation) that changes
    # `settings.suite_encryption_keys` is honoured without reloading the module. Building
    # a MultiFernet is a few microseconds; nothing calls this in a loop that matters.
    return MultiFernet([Fernet(key) for key in settings.encryption_key_list])


def encrypt(plaintext: str) -> str:
    """Encrypt with the current key. An empty value stays empty, meaning "no key"."""
    if not plaintext:
        return ""
    return PREFIX + _multi().encrypt(plaintext.encode()).decode()


def decrypt(stored: str) -> str:
    """Decrypt with whichever configured key wrote it.

    Returns "" for a value nothing can read, and logs it, because the caller's next
    step ("no key configured") is the honest answer either way. Raising here would turn
    one corrupt row into a broken provider list.
    """
    if not stored:
        return ""
    if not stored.startswith(PREFIX):
        logger.warning("A stored secret is not encrypted; treating it as unset")
        return ""
    try:
        return _multi().decrypt(stored[len(PREFIX):].encode()).decode()
    except InvalidToken:
        logger.warning(
            "A stored secret could not be decrypted with any SUITE_ENCRYPTION_KEYS entry; "
            "treating it as unset. Was a key removed before its values were re-encrypted?"
        )
        return ""


def reencrypt(stored: str) -> str:
    """Re-encrypt a stored value under the current (first) key, for key rotation.

    `MultiFernet.rotate` keeps the original timestamp, which matters to nobody here, and
    it raises on a value no key can read. That is the right behaviour for a rotation
    run: carrying on would drop the value silently when the old key goes.
    """
    if not stored:
        return ""
    if not stored.startswith(PREFIX):
        raise ValueError("Refusing to re-encrypt a value that was never encrypted")
    return PREFIX + _multi().rotate(stored[len(PREFIX):].encode()).decode()


def decrypt_legacy(stored: str, jwt_secret: str) -> str:
    """Read a key stored by Notes, GAM or GVC.

    All three use `Fernet(urlsafe_b64(sha256(JWT_SECRET_KEY)))` and an `enc:` prefix, and
    all three accept a plaintext value from before they encrypted. They share one JWT
    secret, so one function reads all three databases.
    """
    if not stored:
        return ""
    if not stored.startswith(PREFIX):
        return stored
    key = base64.urlsafe_b64encode(hashlib.sha256(jwt_secret.encode()).digest())
    return Fernet(key).decrypt(stored[len(PREFIX):].encode()).decode()
