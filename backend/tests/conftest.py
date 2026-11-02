"""Shared fixtures, trimmed from GVC's.

Tests go through real HTTP from the first commit: gecko-notes tests its routers by
calling handler functions directly, so its status codes, response models and auth layer
are largely unverified.

Both secrets are set before any app module is imported: app.config refuses a weak or
missing JWT_SECRET_KEY, and a missing or malformed SUITE_ENCRYPTION_KEYS, at import time.
The encryption key is a fixed, valid Fernet key that exists only in this file.
"""

import os

os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-not-used-in-production")
os.environ.setdefault("SUITE_ENCRYPTION_KEYS", "dGVzdC1vbmx5LWZlcm5ldC1rZXktMzItYnl0ZXMhISE=")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from app.auth import UserCtx, current_user  # noqa: E402
from app.database import get_session  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402

TEST_USER_ID = "user-under-test"


@pytest.fixture(name="engine")
def engine_fixture():
    """An in-memory database shared across connections (StaticPool), with foreign keys
    enforced as production enforces them."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _enforce_foreign_keys(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(engine)
    yield engine
    SQLModel.metadata.drop_all(engine)


@pytest.fixture(name="session")
def session_fixture(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture(name="client")
def client_fixture(session):
    """An unauthenticated client. Requests to a protected route get a real 401."""
    fastapi_app.dependency_overrides[get_session] = lambda: session
    with TestClient(fastapi_app) as client:
        yield client
    fastapi_app.dependency_overrides.clear()


def _as(user_id: str):
    return lambda: UserCtx(id=user_id, username=user_id)


@pytest.fixture(name="auth_client")
def auth_client_fixture(session):
    """Signed in by overriding the dependency. Tests about token verification build a
    real token instead (test_auth.py), so this override never hides a broken auth path."""
    fastapi_app.dependency_overrides[get_session] = lambda: session
    fastapi_app.dependency_overrides[current_user] = _as(TEST_USER_ID)
    with TestClient(fastapi_app) as client:
        yield client
    fastapi_app.dependency_overrides.clear()


ADMIN_ID = "admin-user"


@pytest.fixture(name="switch_user")
def switch_user_fixture():
    """Call with a user id to make the next requests come from that user."""

    def switch(user_id: str) -> None:
        fastapi_app.dependency_overrides[current_user] = _as(user_id)

    return switch


@pytest.fixture(name="admin")
def admin_fixture(monkeypatch):
    """Make ADMIN_ID an administrator. Switch to them with `switch_user(ADMIN_ID)`."""
    from app.config import settings

    monkeypatch.setattr(settings, "admin_users", ADMIN_ID)
    return ADMIN_ID


@pytest.fixture(name="catalog")
def catalog_fixture(session):
    """The catalogue as the seed migration writes it.

    Read from the migration itself rather than restated, so a test about pricing tests
    the data production actually has.
    """
    import importlib.util
    import json
    from pathlib import Path

    from app.models.ai import CatalogEntry

    path = next(Path(__file__).parents[1].glob("alembic/versions/*seed_the_model_catalogue.py"))
    spec = importlib.util.spec_from_file_location("seed_catalogue", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for index, (vendor, model_id, label, caps, max_tokens, context, dims, pricing, note) in enumerate(
        module.ROWS
    ):
        session.add(
            CatalogEntry(
                vendor=vendor,
                model_id=model_id,
                label=label,
                capabilities=json.dumps(sorted(caps)),
                max_tokens=max_tokens,
                context_window=context,
                embed_dims=dims,
                pricing=json.dumps(pricing, sort_keys=True) if pricing else None,
                note=note,
                sort_order=index,
            )
        )
    session.commit()


@pytest.fixture(name="vendors")
def vendors_fixture(monkeypatch):
    """Every vendor, faked. Each test sets `vendors.respond` and reads `vendors.calls`."""
    import httpx

    from app import outbound

    class FakeVendors:
        def __init__(self):
            self.calls: list[httpx.Request] = []
            self.respond = lambda request: httpx.Response(200, json={})

        def handler(self, request: httpx.Request) -> httpx.Response:
            self.calls.append(request)
            return self.respond(request)

    fake = FakeVendors()
    monkeypatch.setattr(outbound, "TRANSPORT", httpx.MockTransport(fake.handler))
    return fake
