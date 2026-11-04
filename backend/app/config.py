"""Every environment-derived setting, resolved once.

Copied from GVC, which copied it from GAM. gecko-notes reads `os.getenv` at module scope
in a dozen places, so its tests monkeypatch module globals and whichever module imported
a value first wins. One settings object avoids that: tests override the object.

Instantiated once as `settings` at the bottom. Import that, not the class.
"""

from functools import lru_cache
from pathlib import Path
from typing import List

from cryptography.fernet import Fernet
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

# The weak defaults the sibling apps ship, refused there and refused here. Sharing the
# list means a copied .env carrying one fails in every app rather than silently signing
# tokens with a published secret in one of them.
_WEAK_SECRETS = frozenset({
    "",
    "change-me",
    "gecko-notes-secret-change-in-production",
    "gam-secret-change-in-production",
    "gvc-secret-change-in-production",
    "gecko-suite-secret-change-in-production",
})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        # Every value this model refuses is a secret or sits next to one, and pydantic
        # prints the rejected input in its error by default. That error is what a failed
        # start writes to the container log, so a mistyped encryption key would land
        # there in full.
        hide_input_in_errors=True,
    )

    # ─── identity ────────────────────────────────────────────────────────────
    # Must match gecko-notes', GAM's and GVC's. Until identity moves here (M6) the suite,
    # like GAM and GVC, verifies the HS256 tokens Notes signs. After the move it signs
    # them, with the same secret and the same claims, so the other apps change nothing.
    #
    # validate_default is load-bearing: pydantic v2 skips validators on defaults, so
    # without it an unset secret would sail past _reject_weak_secret.
    jwt_secret_key: str = Field(default="", validate_default=True)
    jwt_algorithm: str = "HS256"
    # The cookie gecko-notes sets on the parent domain. Read only, until M6.
    auth_cookie_name: str = "gecko_session"
    # Where an unauthenticated visitor is sent to sign in, until the suite has its own
    # sign-in page (M6).
    notes_base_url: str = "https://notes.geckopico.com"

    # ─── secrets at rest ─────────────────────────────────────────────────────
    # Comma-separated Fernet keys. The first encrypts; every one of them decrypts, so a
    # key is rotated by putting a new one in front and dropping the old one once every
    # stored value has been re-encrypted (`app.crypto.reencrypt`; M1 adds the command
    # that walks the tables with it).
    #
    # Separate from JWT_SECRET_KEY on purpose. Notes, GAM and GVC derive their Fernet key
    # from the JWT secret, so rotating the secret there makes every stored provider key
    # unreadable. This service holds every provider key in the suite; that coupling here
    # would make a leaked session secret also a leaked key vault, and a rotated one a
    # wiped vault.
    suite_encryption_keys: str = Field(default="", validate_default=True)

    # ─── storage ─────────────────────────────────────────────────────────────
    database_url: str = ""
    # Persona avatars (M1) and anything else the suite keeps on disk. `data/db` stays the
    # database's own directory inside it.
    data_dir: str = ""

    # ─── http ────────────────────────────────────────────────────────────────
    cors_origin: str = ""

    # ─── fal.ai ──────────────────────────────────────────────────────────────
    # Settings rather than constants so a test, or a proxy, can point them elsewhere.
    fal_queue_base_url: str = "https://queue.fal.run"
    fal_api_base_url: str = "https://api.fal.ai"

    # ─── Deepgram ────────────────────────────────────────────────────────────
    deepgram_base_url: str = "https://api.deepgram.com"
    # What one transcription request may carry. GAM sends mono FLAC, about 20 MB an hour, so
    # this is many hours; past it the caller is told to send the recording in parts. Held in
    # memory for the length of the request, which is why it is bounded at all.
    stt_max_upload_mb: int = 300

    # ─── administration ──────────────────────────────────────────────────────
    # Comma-separated user ids or usernames, matched exactly. The token carries no admin
    # claim, so this is how anybody becomes one. Admins register the providers everyone
    # shares (instance scope); everyone else can only add their own.
    admin_users: str = ""

    # ─── development ─────────────────────────────────────────────────────────
    # Bypasses token verification and acts as this user id. Refused unless
    # `environment` is "development", so it cannot be switched on in production by
    # setting one variable.
    environment: str = "production"
    dev_auth_user: str = ""

    @field_validator("jwt_secret_key")
    @classmethod
    def _reject_weak_secret(cls, value: str) -> str:
        if value.strip() in _WEAK_SECRETS:
            raise ValueError(
                "JWT_SECRET_KEY must be set to a strong secret before starting. "
                "It must match the value gecko-notes, GAM and GVC use. "
                "Generate one with: openssl rand -hex 32"
            )
        return value

    @field_validator("suite_encryption_keys")
    @classmethod
    def _require_valid_fernet_keys(cls, value: str) -> str:
        keys = [k.strip() for k in value.split(",") if k.strip()]
        if not keys:
            raise ValueError(
                "SUITE_ENCRYPTION_KEYS must be set before starting: it encrypts every "
                "provider key the suite holds. Generate one with: "
                "python -c 'from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())'"
            )
        for index, key in enumerate(keys):
            try:
                Fernet(key)
            except (ValueError, TypeError) as exc:
                # The index, never the key: this message ends up in container logs.
                raise ValueError(
                    f"SUITE_ENCRYPTION_KEYS entry {index + 1} is not a valid Fernet key "
                    "(32 url-safe base64-encoded bytes)."
                ) from exc
        return value

    @property
    def encryption_key_list(self) -> List[str]:
        return [k.strip() for k in self.suite_encryption_keys.split(",") if k.strip()]

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{REPO_ROOT / 'data' / 'db' / 'suite.db'}"

    @property
    def resolved_data_dir(self) -> Path:
        return Path(self.data_dir) if self.data_dir else REPO_ROOT / "data"

    @property
    def cors_origins(self) -> List[str]:
        return [o.strip() for o in self.cors_origin.split(",") if o.strip()]

    @property
    def admin_user_list(self) -> List[str]:
        return [u.strip() for u in self.admin_users.split(",") if u.strip()]

    @property
    def is_development(self) -> bool:
        return self.environment.strip().lower() in {"development", "dev", "local"}

    @property
    def dev_auth_enabled(self) -> bool:
        """Whether to accept requests with no token at all.

        Two conditions, not one. `DEV_AUTH_USER` alone is not enough: a production
        .env that picked the variable up from a dev template would otherwise turn
        authentication off entirely.
        """
        return bool(self.dev_auth_user) and self.is_development


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
