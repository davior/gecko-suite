"""Say the database is behind, instead of letting the first query explain it.

`alembic upgrade head` runs from `backend/entrypoint.sh` — the Docker entrypoint — and
nowhere else. The app deliberately does not migrate on startup, so a failed migration
stops the container rather than leaving it running against a schema it does not match.
The cost of that choice is that a bare `uvicorn` never migrates at all, which is
invisible until a pull adds a table: then every request touching it dies with
`no such table: …` under several hundred lines of traceback that never mention the
actual problem.

This turns that into one line at boot. It is a diagnostic, not a gate — it never raises,
and it says nothing when the schema is current, so a Docker boot gains no noise.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

BACKEND_ROOT = Path(__file__).resolve().parents[1]

FIX = (
    "Run `alembic upgrade head` from the backend directory. Docker does this in "
    "entrypoint.sh; a bare `uvicorn` does not."
)


@dataclass(frozen=True)
class SchemaState:
    """Where the database is relative to this checkout's migrations."""

    current: Optional[str]
    head: Optional[str]
    behind: int
    # The database's revision is not in this checkout at all — so the *code* is behind,
    # not the schema, and telling anyone to upgrade would be the wrong advice.
    unknown_revision: bool


def schema_state(engine: Engine) -> SchemaState:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    # Absolute rather than relative to the working directory: `cd backend && uvicorn`
    # and the container's WORKDIR are not the same place, and a check that only works
    # from one of them is worse than none. `tests/test_migrations.py` does the same.
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    script = ScriptDirectory.from_config(config)
    head = script.get_current_head()

    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()

    if current == head:
        return SchemaState(current, head, 0, False)

    try:
        # head → current, exclusive of current. With `current` None that walks to base,
        # which is exactly the count for a database nothing has ever been applied to.
        behind = len(list(script.iterate_revisions(head, current)))
    except Exception:
        return SchemaState(current, head, 0, True)

    return SchemaState(current, head, behind, False)


def log_if_behind(engine: Engine) -> None:
    """Warn once at startup if the schema is out of step. Never raises."""
    try:
        state = schema_state(engine)
    except Exception:
        # Swallowed on purpose. A diagnostic that stops the application is strictly
        # worse than no diagnostic, and every reason this can fail — an unreadable
        # script directory, a database that is not up yet — is one the app itself will
        # report far more clearly a moment later.
        logger.debug("Could not determine the database schema version", exc_info=True)
        return

    if state.unknown_revision:
        logger.warning(
            "Database is at migration %s, which this checkout does not contain. The "
            "code is older than the database — check out a newer revision. Do not run "
            "`alembic upgrade head`; it has nothing to apply.",
            state.current,
        )
        return

    if state.current is None:
        logger.warning("Database has no schema — no migrations applied. %s", FIX)
        return

    if state.behind:
        logger.warning(
            "Database is %d migration%s behind (at %s, head is %s). Requests touching "
            "anything new will fail with `no such table`. %s",
            state.behind,
            "" if state.behind == 1 else "s",
            state.current,
            state.head,
            FIX,
        )
