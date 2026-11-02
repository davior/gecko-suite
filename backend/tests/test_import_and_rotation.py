"""The legacy importer, against small databases in each sibling app's schema, and key rotation."""

import base64
import hashlib
import json
import sqlite3

import pytest
from cryptography.fernet import Fernet
from sqlmodel import select

from app import crypto
from app.config import settings
from app.models.ai import AIModel, AIProvider, TaskAssignment
from app.models.library import Recipe, SystemPrompt
from app.models.usage import UsageEvent
from app.models.user import User
from app.tools import import_legacy, rotate_keys

JWT = "the-secret-all-the-apps-share-0123456789"


def sibling_encrypt(value: str, secret: str = JWT) -> str:
    """How Notes, GAM and GVC store a key: Fernet keyed by sha256(JWT_SECRET_KEY)."""
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return "enc:" + Fernet(key).encrypt(value.encode()).decode()


PROVIDER_COLUMNS = ("id TEXT, user_id TEXT, name TEXT, provider_type TEXT, api_key TEXT, base_url TEXT, "
                    "model TEXT, max_tokens INT, supports_images INT, use_anthropic_api INT, "
                    "extra_params TEXT, enabled INT, is_active INT")


def make_db(path, *, providers=(), usage=(), settings_rows=(), usage_extra="", users=(), prompts=(), recipes=()):
    db = sqlite3.connect(path)
    db.execute(f"CREATE TABLE aiprovider ({PROVIDER_COLUMNS})")
    db.execute("CREATE TABLE usageevent (id TEXT, user_id TEXT, kind TEXT, provider TEXT, model TEXT, "
               f"units INT, unit_type TEXT, cost REAL, currency TEXT, cost_estimated INT, created_at TEXT{usage_extra})")
    db.execute("CREATE TABLE usersetting (user_id TEXT, key TEXT, value TEXT)")
    for p in providers:
        db.execute("INSERT INTO aiprovider VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", p)
    for u in usage:
        db.execute(f"INSERT INTO usageevent VALUES ({','.join('?' * len(u))})", u)
    for s in settings_rows:
        db.execute("INSERT INTO usersetting VALUES (?,?,?)", s)
    if users:
        db.execute("CREATE TABLE user (id TEXT, username TEXT, email TEXT, hashed_password TEXT, is_admin INT)")
        for u in users:
            db.execute("INSERT INTO user VALUES (?,?,?,?,?)", u)
    if prompts:
        db.execute("CREATE TABLE systemprompt (id TEXT, name TEXT, content TEXT, is_active INT, sort_order INT, user_id TEXT)")
        for p in prompts:
            db.execute("INSERT INTO systemprompt VALUES (?,?,?,?,?,?)", p)
    if recipes:
        db.execute("CREATE TABLE recipe (id TEXT, user_id TEXT, name TEXT, prompt TEXT, tags TEXT, sort_order INT)")
        for r in recipes:
            db.execute("INSERT INTO recipe VALUES (?,?,?,?,?,?)", r)
    db.commit()
    db.close()
    return path


@pytest.fixture(name="sources")
def sources_fixture(tmp_path):
    claude_key = sibling_encrypt("sk-ant-same-everywhere")
    notes = make_db(
        tmp_path / "notes.db",
        providers=[
            ("n1", "alice", "Claude", "anthropic", claude_key, None, "claude-opus-5-5", 64000, 1, 0, None, 1, 1),
            ("n2", "bob", "Old", "openai", sibling_encrypt("k", secret="a-rotated-secret"), None, "gpt-4o", 1000, 1, 0, None, 1, 0),
        ],
        usage=[("u1", "alice", "ai", "anthropic", "claude-opus-5-5", 1500, "tokens", 0.01, "USD", 1, "2026-10-01T10:00:00")],
        settings_rows=[("alice", "fal_api_key", json.dumps(sibling_encrypt("fal-key")))],
        users=[("alice", "alice", "alice@example.com", "bcrypt-hash", 1)],
        prompts=[("p1", "House style", "Be brief.", 1, 0, "alice")],
        recipes=[("r1", "alice", "Newsletter", "Summarise for a newsletter", json.dumps(["b", "a"]), 0)],
    )
    gam = make_db(
        tmp_path / "gam.db",
        providers=[("g1", "alice", "Claude again", "anthropic", claude_key, None, "claude-sonnet-5-5", 16384, 1, 0,
                    json.dumps({"temperature": 0}), 1, 1)],
        usage=[("u1", "alice", "image", "fal.ai", "flux", 1, "images", 0.05, "USD", 0, "2026-10-02T10:00:00", "asset-9")],
        usage_extra=", asset_id TEXT",
        settings_rows=[
            ("alice", "fal_api_key", json.dumps(sibling_encrypt("fal-key"))),
            ("alice", "embedding_provider", json.dumps("ollama")),
            ("alice", "embedding_model", json.dumps("nomic-embed-text")),
            ("alice", "ollama_base_url", json.dumps("http://gpu-box:11434")),
        ],
    )
    gvc = make_db(
        tmp_path / "gvc.db",
        providers=[("v1", "carol", "Claude", "anthropic", sibling_encrypt("sk-carol"), None, "claude-opus-5-5", 16384, 1, 0, None, 1, 1)],
    )
    return {"notes": notes, "gam": gam, "gvc": gvc}


def _run(session, sources, **kw):
    return import_legacy.run(session, sources, JWT, **kw)


def test_one_key_pasted_in_two_apps_becomes_one_provider(session, catalog, sources):
    report = _run(session, sources)
    alice = session.exec(select(AIProvider).where(AIProvider.user_id == "alice", AIProvider.vendor == "anthropic")).all()
    assert len(alice) == 1
    assert crypto.decrypt(alice[0].api_key) == "sk-ant-same-everywhere"
    models = session.exec(select(AIModel).where(AIModel.provider_id == alice[0].id)).all()
    assert {m.model_id for m in models} == {"claude-opus-5-5", "claude-sonnet-5-5"}
    sonnet = next(m for m in models if m.model_id == "claude-sonnet-5-5")
    # Known models get the catalogue's description; the legacy row's params stay with it.
    assert sonnet.label == "Claude Sonnet 5.5" and json.loads(sonnet.params) == {"temperature": 0}
    assert report.warnings and "could not be decrypted" in report.warnings[0]


def test_active_providers_become_routing(session, catalog, sources):
    _run(session, sources)
    rules = session.exec(select(TaskAssignment).where(TaskAssignment.user_id == "alice")).all()
    by_app = {(r.task, r.app): session.get(AIModel, r.model_ref).model_id for r in rules}
    # Different models were active in Notes and GAM, so each app keeps its own.
    assert by_app[("chat.assistant", "notes")] == "claude-opus-5-5"
    assert by_app[("chat.assistant", "gam")] == "claude-sonnet-5-5"
    # Carol used one app, so her one model is routed for every app.
    carol = session.exec(select(TaskAssignment).where(TaskAssignment.user_id == "carol")).one()
    assert (carol.task, carol.app) == ("chat.assistant", None)


def test_gam_embeddings_and_media_keys_come_across(session, catalog, sources):
    _run(session, sources)
    fal = session.exec(select(AIProvider).where(AIProvider.vendor == "fal")).all()
    assert len(fal) == 1 and crypto.decrypt(fal[0].api_key) == "fal-key"  # same key in two apps: one provider
    ollama = session.exec(select(AIProvider).where(AIProvider.vendor == "ollama")).one()
    assert ollama.base_url == "http://gpu-box:11434"
    embed = session.exec(select(TaskAssignment).where(TaskAssignment.task == "embed.text")).one()
    assert embed.app == "gam" and session.get(AIModel, embed.model_ref).model_id == "nomic-embed-text"


def test_usage_prompts_recipes_and_users(session, catalog, sources):
    _run(session, sources)
    events = {e.app: e for e in session.exec(select(UsageEvent)).all()}
    assert events["notes"].cost == 0.01 and events["notes"].units == 1500
    assert (events["gam"].vendor, events["gam"].subject_ref, events["gam"].cost_estimated) == ("fal", "gam:asset:asset-9", False)
    prompt = session.exec(select(SystemPrompt)).one()
    assert (prompt.user_id, prompt.app, prompt.is_default) == ("alice", "notes", True)
    assert json.loads(session.exec(select(Recipe)).one().tags) == ["a", "b"]
    alice = session.get(User, "alice")
    assert (alice.username, alice.email) == ("alice", "alice@example.com")


def test_an_undecryptable_key_is_imported_without_it(session, catalog, sources):
    _run(session, sources)
    bob = session.exec(select(AIProvider).where(AIProvider.user_id == "bob")).one()
    assert bob.api_key == ""


def test_running_twice_adds_nothing(session, catalog, sources):
    _run(session, sources)
    counts = [len(session.exec(select(m)).all()) for m in (AIProvider, AIModel, TaskAssignment, UsageEvent, SystemPrompt)]
    second = _run(session, sources)
    assert [len(session.exec(select(m)).all()) for m in (AIProvider, AIModel, TaskAssignment, UsageEvent, SystemPrompt)] == counts
    assert (second.providers, second.models, second.usage) == (0, 0, 0) and second.skipped > 0


def test_the_sources_are_not_touched(session, catalog, sources):
    before = {app: path.read_bytes() for app, path in sources.items()}
    _run(session, sources)
    assert {app: path.read_bytes() for app, path in sources.items()} == before


def test_a_dry_run_writes_nothing(session, catalog, sources):
    report = _run(session, sources, dry_run=True)
    assert report.providers > 0
    assert session.exec(select(AIProvider)).all() == []


# ─── rotation ────────────────────────────────────────────────────────────────


def test_rotation_moves_every_secret_to_the_new_key(session, monkeypatch):
    old = settings.suite_encryption_keys
    session.add(AIProvider(scope="user", user_id="u", name="P", vendor="openai", api_key=crypto.encrypt("sk-1")))
    session.commit()

    new = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "suite_encryption_keys", f"{new},{old}")
    assert rotate_keys.rotate(session) == 1

    monkeypatch.setattr(settings, "suite_encryption_keys", new)
    assert crypto.decrypt(session.exec(select(AIProvider)).one().api_key) == "sk-1"


def test_rotation_stops_and_changes_nothing_on_an_unreadable_value(session, monkeypatch):
    session.add(AIProvider(scope="user", user_id="u", name="A", vendor="openai", api_key=crypto.encrypt("sk-1")))
    session.commit()
    stored = session.exec(select(AIProvider)).one().api_key
    monkeypatch.setattr(settings, "suite_encryption_keys", Fernet.generate_key().decode())
    with pytest.raises(SystemExit):
        rotate_keys.rotate(session)
    assert session.exec(select(AIProvider)).one().api_key == stored
