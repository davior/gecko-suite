"""The migration chain applies to an empty database, matches the models, and reverses.

Copied from GVC: gecko-notes never exercises its schema setup, so a broken
statement would only surface on deploy.
"""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlmodel import SQLModel

BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(name="alembic_config")
def alembic_config_fixture(tmp_path, monkeypatch):
    db_path = tmp_path / "migrate.db"
    url = f"sqlite:///{db_path}"

    # env.py reads the URL from settings and builds on app.database's engine, so both
    # have to point at the temp database for the run to be isolated.
    from sqlalchemy import event
    from sqlmodel import create_engine

    from app import database
    from app.config import settings

    monkeypatch.setattr(settings, "database_url", url)
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _enforce_foreign_keys(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    monkeypatch.setattr(database, "engine", engine)

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    # Must not reconfigure this process's logging (see alembic/env.py).
    config.attributes["configure_logger"] = False
    return config, db_path, engine


def _tables(db_path: Path) -> set:
    with sqlite3.connect(db_path) as conn:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_upgrade_creates_the_schema_the_models_describe(alembic_config):
    config, db_path, engine = alembic_config
    command.upgrade(config, "head")
    assert "user" in _tables(db_path)

    import app.models  # noqa: F401

    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), SQLModel.metadata)
    # A model changed without a migration shows up here, not on deploy.
    assert diff == []


def test_downgrade_reverses_cleanly(alembic_config):
    config, db_path, _ = alembic_config
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    assert _tables(db_path) <= {"alembic_version"}
