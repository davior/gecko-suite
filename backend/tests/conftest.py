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
