"""Engine, session factory and the SQLite pragmas that make concurrent reads work.

Schema changes are Alembic migrations (`alembic/versions/`), not `create_all` and not
a sequence of guarded `ALTER TABLE` statements. gecko-notes took the latter route and
ended up with 650 lines of `try: … except: pass` carrying data backfills, no version
table and no way back — this app starts on the other side of that decision.
"""

import logging
from pathlib import Path
from typing import Iterator

from sqlalchemy import event
from sqlmodel import Session, create_engine

from app.config import settings

logger = logging.getLogger(__name__)

DATABASE_URL = settings.resolved_database_url

if DATABASE_URL.startswith("sqlite:///"):
    Path(DATABASE_URL.replace("sqlite:///", "", 1)).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    DATABASE_URL,
    # SQLite refuses a connection used from a thread other than the one that opened
    # it. Background workers run on their own threads and open their own sessions,
    # so the check has to come off.
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    echo=False,
)


if DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record):
        """WAL so a long read (a usage report over months of events) does not block a write.

        `synchronous=NORMAL` is the usual companion: with WAL it still survives an
        application crash, and only risks the last transactions on a power loss —
        which is the right trade for a single-host service.
        """
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def release_connection(session: Session) -> None:
    """Hand the session's pooled connection back before waiting on someone else.

    A session that has read anything holds its connection until the transaction ends, and
    a request's session lives as long as the request. The gateway reads its routing, then
    waits seconds or minutes on a vendor; holding a connection all that time meant fifteen
    calls in flight (the pool's 5 + 10) left every other request, `/resolve` included,
    waiting 30 s for one and failing as a bare 500 (tests/test_pool_release.py).

    Committed without expiring what was loaded, so the rows read so far stay readable
    during the call without a query that would take a connection again. Anything written
    before this point is committed with it; a later use of the session checks out a
    connection afresh, briefly, which is how usage is recorded at the end.
    """
    if not session.in_transaction():
        return
    expire, session.expire_on_commit = session.expire_on_commit, False
    try:
        session.commit()
    finally:
        session.expire_on_commit = expire


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
