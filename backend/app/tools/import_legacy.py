"""Bring the AI settings and history of Notes, GAM and GVC into the suite.

    python -m app.tools.import_legacy --notes notes.db --gam gam.db --gvc gvc.db [--dry-run]

Any subset of the three. Each database is opened read-only and nothing in it changes.
Run it again and it adds only what is new: every imported row's id is derived from its
source row, so a second run finds the first run's rows and skips them.

What comes across, and how:

- **Providers.** Each app's `aiprovider` row is one key and one model. Rows with the same
  owner, vendor, endpoint and (decrypted) key, in any of the three apps, become *one*
  suite provider with several models. That is the point of the suite: the key you pasted
  three times is now entered once.
- **Active providers become routing.** Each app's active provider becomes the user's
  `chat.assistant` model for that app, or for every app when all of them used the same
  model.
- **GAM's embedding settings** become a provider, an embedding model and `embed.text`
  routing for GAM.
- **fal and Deepgram keys** (Notes and GAM settings) become providers of those vendors,
  ready for media and speech in M3.
- **Usage history** keeps its costs, tagged with the app it came from. GAM's asset ids
  become `gam:asset:<id>` and GVC's project ids `gvc:project:<id>`.
- **Notes' system prompts and recipes**, as the user's own, for Notes.
- **User rows** for everyone seen, with Notes' usernames and emails (never passwords;
  identity moves in M6).

A key that cannot be decrypted (a database from before a JWT secret change) is imported
as "no key set" with a warning, so the provider is visible and needs only the key again.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from cryptography.fernet import InvalidToken
from sqlmodel import Session, select

from app import crypto
from app.config import settings
from app.models.ai import AIModel, AIProvider, CatalogEntry, TaskAssignment
from app.models.library import Recipe, SystemPrompt
from app.models.usage import UsageEvent
from app.models.user import User
from app.services.registry import catalog_entry_for, dump_json
from app.vocab import SCOPE_INSTANCE, SCOPE_USER, VENDORS

logger = logging.getLogger("import_legacy")

# Fixed, so ids derived from it are the same on every run and every machine.
_NAMESPACE = uuid.UUID("6f1c1d3e-2a7b-4f4e-9a51-2f0e5b7c9d10")

# The vendor names usage rows were recorded under, mapped to the suite's.
_USAGE_VENDOR = {"fal.ai": "fal", "fal": "fal", "deepgram": "deepgram"}
# Base URLs that mean "the vendor's default" and are stored as none.
_DEFAULT_URLS = {"https://api.openai.com", "https://api.openai.com/v1", "http://localhost:11434"}


def derived_id(*parts: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, ":".join(parts)))


@dataclass
class Report:
    providers: int = 0
    models: int = 0
    assignments: int = 0
    usage: int = 0
    prompts: int = 0
    recipes: int = 0
    users: int = 0
    accounts: int = 0
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        logger.warning(message)

    def lines(self) -> list[str]:
        return [
            f"providers created: {self.providers}",
            f"models created: {self.models}",
            f"routing rules created: {self.assignments}",
            f"usage events imported: {self.usage}",
            f"system prompts imported: {self.prompts}",
            f"recipes imported: {self.recipes}",
            f"users created: {self.users}",
            f"accounts imported (can sign in here): {self.accounts}",
            f"already present, skipped: {self.skipped}",
            *[f"warning: {w}" for w in self.warnings],
        ]


# ─── reading the old databases ───────────────────────────────────────────────


def open_readonly(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(f"No such database: {path}")
    # mode=ro: an importer that could write would be one typo from damaging the source.
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def rows(connection: sqlite3.Connection, table: str) -> list[dict]:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not exists:
        return []
    return [dict(r) for r in connection.execute(f'SELECT * FROM "{table}"')]


def parse_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value)).replace(tzinfo=None)
    except (TypeError, ValueError):
        return datetime(1970, 1, 1)


def setting(value: Any) -> Any:
    """A UserSetting value: JSON text in Notes and GAM alike."""
    try:
        return json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return value


# ─── importer ────────────────────────────────────────────────────────────────


@dataclass
class LegacyProvider:
    app: str
    id: str
    user_id: str
    name: str
    vendor: str
    api_key: str  # decrypted
    base_url: Optional[str]
    use_anthropic_api: bool
    model: str
    max_tokens: Optional[int]
    supports_images: bool
    extra_params: Optional[str]
    enabled: bool
    is_active: bool


class Importer:
    def __init__(self, session: Session, jwt_secret: str, report: Report):
        self.session = session
        self.jwt_secret = jwt_secret
        self.report = report

    # ── helpers ──

    def decrypt(self, stored: Any, where: str) -> str:
        if not stored:
            return ""
        try:
            return crypto.decrypt_legacy(str(stored), self.jwt_secret)
        except (InvalidToken, ValueError):
            self.report.warn(f"{where}: the key could not be decrypted with this JWT secret; "
                             "imported without a key")
            return ""

    def ensure_user(self, user_id: str, *, username: str = "", email: Optional[str] = None,
                    is_admin: bool = False) -> None:
        user = self.session.get(User, user_id)
        if user is None:
            self.session.add(User(id=user_id, username=username, email=email, is_admin=is_admin))
            self.report.users += 1
        else:
            user.username = user.username or username
            user.email = user.email or email
            self.session.add(user)

    def import_account(self, row: dict) -> None:
        """Make a Notes account one that signs in here (M6), with the id, password and second
        factor it had, so nobody has to do anything at the cutover.

        An account that already has a password here is left alone: it signed in here since
        an earlier import, and Notes' copy is the older one.
        """
        user = self.session.get(User, row["id"])
        if user is None or user.hashed_password or not row.get("hashed_password"):
            return
        user.username = row.get("username") or user.username
        user.email = row.get("email") or user.email
        user.hashed_password = row["hashed_password"]
        user.is_active = bool(row.get("is_active", True))
        user.is_admin = bool(row.get("is_admin"))
        user.email_verified = bool(row.get("email_verified", True))
        user.created_at = parse_time(row.get("created_at")) if row.get("created_at") else user.created_at
        user.last_login = parse_time(row["last_login"]) if row.get("last_login") else None
        method = row.get("two_factor_method") or None
        secret = None
        if method == "totp":
            plain = self.decrypt(row.get("totp_secret"), f"Notes account {user.username!r}: authenticator")
            if plain:
                secret = crypto.encrypt(plain)
            else:
                # Importing the method without a usable secret would lock them out. Off, and
                # said, so an administrator can have them set it up again.
                method = None
                self.report.warn(f"Notes account {user.username!r}: two-factor turned off, because its "
                                 "authenticator secret could not be decrypted; ask them to set it up again")
        user.two_factor_method, user.totp_secret = method, secret
        self.session.add(user)
        self.report.accounts += 1

    def import_sign_up_settings(self, connection: sqlite3.Connection) -> None:
        """Notes' two sign-up switches, unless they were already set here."""
        from app.accounts import instance
        from app.models.user import InstanceSetting

        for row in rows(connection, "appsetting"):
            if row.get("key") in instance.DEFAULTS and self.session.get(InstanceSetting, row["key"]) is None:
                instance.put(self.session, row["key"], bool(setting(row.get("value"))))

    def find_provider(self, user_id: str, vendor: str, base_url: Optional[str], use_anthropic_api: bool,
                      api_key: str) -> Optional[AIProvider]:
        """An existing suite provider with this exact key and endpoint, from an earlier
        run or from someone setting it up by hand first."""
        candidates = self.session.exec(select(AIProvider).where(
            AIProvider.scope == SCOPE_USER, AIProvider.user_id == user_id, AIProvider.vendor == vendor,
        )).all()
        for candidate in candidates:
            if ((candidate.base_url or None) == base_url
                    and bool(candidate.use_anthropic_api) == bool(use_anthropic_api)
                    and crypto.decrypt(candidate.api_key) == api_key):
                return candidate
        return None

    def provider_for(self, *, user_id: str, vendor: str, name: str, api_key: str, base_url: Optional[str],
                     use_anthropic_api: bool = False, enabled: bool = True) -> AIProvider:
        existing = self.find_provider(user_id, vendor, base_url, use_anthropic_api, api_key)
        if existing is not None:
            return existing
        provider = AIProvider(scope=SCOPE_USER, user_id=user_id, name=name, vendor=vendor,
                              api_key=crypto.encrypt(api_key), base_url=base_url,
                              use_anthropic_api=use_anthropic_api, enabled=enabled)
        self.session.add(provider)
        self.session.flush()
        self.report.providers += 1
        return provider

    def model_for(self, provider: AIProvider, model_id: str, *, capabilities: Iterable[str],
                  max_tokens: Optional[int] = None, embed_dims: Optional[int] = None,
                  params: Optional[str] = None) -> AIModel:
        existing = self.session.exec(select(AIModel).where(
            AIModel.provider_id == provider.id, AIModel.model_id == model_id)).first()
        if existing is not None:
            return existing
        entry = catalog_entry_for(self.session, provider.vendor, model_id)
        exact = entry if entry is not None and not entry.model_id.endswith("*") else None
        model = AIModel(
            provider_id=provider.id, model_id=model_id, label=exact.label if exact else "",
            # The catalogue knows better than a legacy row what a known model can do; a
            # model it does not know gets what the row said about it.
            capabilities=exact.capabilities if exact else (dump_json(sorted(set(capabilities))) or "[]"),
            max_tokens=max_tokens or (exact.max_tokens if exact else None),
            context_window=exact.context_window if exact else None,
            embed_dims=embed_dims or (exact.embed_dims if exact else None),
            params=params,
        )
        self.session.add(model)
        self.session.flush()
        self.report.models += 1
        return model

    def route(self, user_id: str, task: str, app: Optional[str], model: AIModel) -> None:
        existing = self.session.exec(select(TaskAssignment).where(
            TaskAssignment.scope == SCOPE_USER, TaskAssignment.user_id == user_id, TaskAssignment.task == task,
        )).all()
        if any(row.app == app for row in existing):
            # The user may have set this up in the suite already; theirs wins.
            self.report.skipped += 1
            return
        self.session.add(TaskAssignment(scope=SCOPE_USER, user_id=user_id, app=app, task=task, model_ref=model.id))
        self.report.assignments += 1

    # ── providers ──

    def legacy_providers(self, app: str, connection: sqlite3.Connection) -> list[LegacyProvider]:
        found = []
        for row in rows(connection, "aiprovider"):
            if not row.get("user_id"):
                self.report.warn(f"{app}: provider {row.get('name')!r} has no owner; skipped")
                continue
            vendor = row.get("provider_type") or ""
            if vendor not in VENDORS:
                self.report.warn(f"{app}: provider {row.get('name')!r} has unknown type {vendor!r}; skipped")
                continue
            base_url = (row.get("base_url") or "").rstrip("/") or None
            if base_url in _DEFAULT_URLS or vendor in ("anthropic", "deepseek"):
                # The fixed vendors never read it; a default URL is no URL.
                base_url = None
            found.append(LegacyProvider(
                app=app, id=row["id"], user_id=row["user_id"], name=row.get("name") or vendor,
                vendor=vendor, api_key=self.decrypt(row.get("api_key"), f"{app} provider {row.get('name')!r}"),
                base_url=base_url, use_anthropic_api=bool(row.get("use_anthropic_api")),
                model=row.get("model") or "", max_tokens=row.get("max_tokens"),
                supports_images=bool(row.get("supports_images")), extra_params=row.get("extra_params") or None,
                enabled=bool(row.get("enabled", True)), is_active=bool(row.get("is_active")),
            ))
        return found

    def import_providers(self, legacy: list[LegacyProvider]) -> None:
        active: dict[str, dict[str, AIModel]] = defaultdict(dict)
        for row in legacy:
            self.ensure_user(row.user_id)
            provider = self.provider_for(
                user_id=row.user_id, vendor=row.vendor, name=row.name, api_key=row.api_key,
                base_url=row.base_url, use_anthropic_api=row.use_anthropic_api, enabled=row.enabled,
            )
            if not row.model:
                continue
            capabilities = {"chat", "tools"} | ({"vision"} if row.supports_images else set())
            # A legacy row's extra_params belonged to that model, so they go on the model.
            model = self.model_for(provider, row.model, capabilities=capabilities,
                                   max_tokens=row.max_tokens, params=row.extra_params)
            if row.is_active and row.enabled:
                active[row.user_id][row.app] = model

        for user_id, by_app in active.items():
            models = {m.id for m in by_app.values()}
            if len(models) == 1:
                self.route(user_id, "chat.assistant", None, next(iter(by_app.values())))
            else:
                for app, model in by_app.items():
                    self.route(user_id, "chat.assistant", app, model)

    # ── settings: embeddings, fal, Deepgram ──

    def import_settings(self, app: str, connection: sqlite3.Connection) -> None:
        by_user: dict[str, dict[str, Any]] = defaultdict(dict)
        for row in rows(connection, "usersetting"):
            by_user[row["user_id"]][row["key"]] = setting(row["value"])

        for user_id, values in by_user.items():
            for key, vendor, label in (("fal_api_key", "fal", "fal.ai"), ("deepgram_api_key", "deepgram", "Deepgram")):
                api_key = self.decrypt(values.get(key), f"{app} {label} key for {user_id}")
                if api_key:
                    self.ensure_user(user_id)
                    self.provider_for(user_id=user_id, vendor=vendor, name=f"{label} (from {app})",
                                      api_key=api_key, base_url=None)
            if app == "gam" and values.get("embedding_provider") in ("openai", "ollama"):
                self.import_gam_embeddings(user_id, values)

    def import_gam_embeddings(self, user_id: str, values: dict) -> None:
        self.ensure_user(user_id)
        if values["embedding_provider"] == "ollama":
            base_url = (values.get("ollama_base_url") or "").rstrip("/") or None
            provider = self.provider_for(user_id=user_id, vendor="ollama", name="Ollama (from GAM)",
                                         api_key="", base_url=base_url)
            model_id = values.get("embedding_model") or "nomic-embed-text"
        else:
            api_key = self.decrypt(values.get("openai_api_key"), f"GAM embedding key for {user_id}")
            base_url = (values.get("embedding_base_url") or "").rstrip("/") or None
            if base_url in _DEFAULT_URLS:
                base_url = None
            provider = self.provider_for(user_id=user_id, vendor="custom" if base_url else "openai",
                                         name="OpenAI embeddings (from GAM)", api_key=api_key, base_url=base_url)
            model_id = values.get("embedding_model") or "text-embedding-3-small"
        dims = values.get("embedding_dimensions")
        model = self.model_for(provider, model_id, capabilities={"embed"},
                               embed_dims=int(dims) if str(dims or "").isdigit() else None)
        self.route(user_id, "embed.text", "gam", model)

    # ── usage, prompts, recipes, users ──

    def import_usage(self, app: str, connection: sqlite3.Connection) -> None:
        for row in rows(connection, "usageevent"):
            event_id = derived_id(app, "usage", str(row["id"]))
            if self.session.get(UsageEvent, event_id) is not None:
                self.report.skipped += 1
                continue
            subject = None
            if row.get("asset_id"):
                subject = f"gam:asset:{row['asset_id']}"
            elif row.get("project_id"):
                subject = f"gvc:project:{row['project_id']}"
            vendor = row.get("provider") or ""
            self.ensure_user(row["user_id"])
            self.session.add(UsageEvent(
                id=event_id, user_id=row["user_id"], app=app, kind=row.get("kind") or "ai",
                vendor=_USAGE_VENDOR.get(vendor, vendor), provider_name=vendor, model=row.get("model") or "",
                units=row.get("units") or 0, unit_type=row.get("unit_type") or "",
                # The siblings kept one token total, not input and output: left unsplit
                # rather than guessed.
                cost=row.get("cost"), currency=row.get("currency"),
                cost_estimated=True if row.get("cost_estimated") is None else bool(row["cost_estimated"]),
                external_ref=row.get("external_ref"), subject_ref=subject,
                created_at=parse_time(row.get("created_at")),
            ))
            self.report.usage += 1

    def import_notes_library(self, connection: sqlite3.Connection) -> None:
        for row in rows(connection, "user"):
            self.ensure_user(row["id"], username=row.get("username") or "", email=row.get("email"),
                             is_admin=bool(row.get("is_admin")))
            self.import_account(row)
        self.import_sign_up_settings(connection)
        for row in rows(connection, "systemprompt"):
            prompt_id = derived_id("notes", "prompt", str(row["id"]))
            if self.session.get(SystemPrompt, prompt_id) is not None:
                self.report.skipped += 1
                continue
            owner = row.get("user_id")
            self.session.add(SystemPrompt(
                id=prompt_id, scope=SCOPE_USER if owner else SCOPE_INSTANCE, user_id=owner or None, app="notes",
                name=row.get("name") or "Untitled", content=row.get("content") or "",
                is_default=bool(row.get("is_active")), sort_order=row.get("sort_order") or 0,
            ))
            self.report.prompts += 1
        for row in rows(connection, "recipe"):
            recipe_id = derived_id("notes", "recipe", str(row["id"]))
            if self.session.get(Recipe, recipe_id) is not None:
                self.report.skipped += 1
                continue
            tags = setting(row.get("tags") or "[]")
            self.session.add(Recipe(
                id=recipe_id, scope=SCOPE_USER, user_id=row["user_id"], app="notes", name=row.get("name") or "Untitled",
                prompt=row.get("prompt") or "",
                tags=(dump_json(sorted(set(tags))) or "[]") if isinstance(tags, list) else "[]",
                sort_order=row.get("sort_order") or 0,
            ))
            self.report.recipes += 1


def run(session: Session, sources: dict[str, Path], jwt_secret: str, *, dry_run: bool = False) -> Report:
    report = Report()
    if session.exec(select(CatalogEntry)).first() is None:
        report.warn("The model catalogue is empty; run `alembic upgrade head` first so imported "
                    "models get their labels, limits and prices")
    importer = Importer(session, jwt_secret, report)
    connections = {app: open_readonly(path) for app, path in sources.items()}
    try:
        # Notes first, so its usernames and emails land on the user rows before the
        # other apps create bare ones.
        if "notes" in connections:
            importer.import_notes_library(connections["notes"])
        legacy: list[LegacyProvider] = []
        for app, connection in connections.items():
            legacy.extend(importer.legacy_providers(app, connection))
        importer.import_providers(legacy)
        for app, connection in connections.items():
            importer.import_settings(app, connection)
            importer.import_usage(app, connection)
        if dry_run:
            session.rollback()
        else:
            session.commit()
    finally:
        for connection in connections.values():
            connection.close()
    return report


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--notes", type=Path, help="gecko-notes' database (data/db/notes.db)")
    parser.add_argument("--gam", type=Path, help="GAM's database (data/db/gam.db)")
    parser.add_argument("--gvc", type=Path, help="GVC's database (data/db/gvc.db)")
    parser.add_argument("--jwt-secret", help="the JWT_SECRET_KEY the apps share (default: this suite's)")
    parser.add_argument("--dry-run", action="store_true", help="report what would happen, change nothing")
    args = parser.parse_args(argv)

    sources = {app: path for app, path in (("notes", args.notes), ("gam", args.gam), ("gvc", args.gvc)) if path}
    if not sources:
        parser.error("name at least one of --notes, --gam, --gvc")

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from app.database import engine

    with Session(engine) as session:
        report = run(session, sources, args.jwt_secret or settings.jwt_secret_key, dry_run=args.dry_run)
    print(("DRY RUN, nothing written\n" if args.dry_run else "") + "\n".join(report.lines()))
    # A fingerprint of which secret was used, never the secret, for a support thread.
    print(f"jwt secret fingerprint: {hashlib.sha256((args.jwt_secret or settings.jwt_secret_key).encode()).hexdigest()[:8]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
