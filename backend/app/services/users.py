"""The local record of a Gecko Notes account.

Until M6 the suite does not own identity: Notes signs the token, the suite verifies it.
But a verified token is not a user row, and until one exists the suite knows a `sub`
string and nothing else — no name to show in a header, no record that this person has
ever been here, nothing for an admin or usage view to count.

So the first time a subject is seen, a shadow row is written from the token's claims.
It is deliberately thin, because the token is thin: gecko-notes signs `{sub, username,
exp}` and nothing more. Anything richer (email, admin) has to come from Notes' own
`/api/auth/session`, which the browser can call directly — doing it server-side would
put a second service on the suite's request path and let Notes being slow make every
AI call in the suite slow.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlmodel import Session

from app.auth import UserCtx
from app.clock import utcnow
from app.models.user import User

logger = logging.getLogger(__name__)

# How stale `last_seen` may get before it is rewritten. Without a threshold this would
# be a write on every request — a pointless one, since nothing needs minute-accurate
# presence, and SQLite has one writer.
LAST_SEEN_REFRESH = timedelta(hours=1)


def ensure_user(session: Session, ctx: UserCtx) -> User:
    """Return the local row for this caller, creating it if this is their first visit.

    Never raises on a write it could not make: the suite's actual authority to act comes from
    the verified token, not from this row, so a failure to record the visit must not
    turn into a failed request.
    """
    user = session.get(User, ctx.id)
    now = utcnow()

    if user is None:
        user = User(id=ctx.id, username=ctx.username or "", created_at=now, last_seen=now)
        session.add(user)
        try:
            session.commit()
            session.refresh(user)
        except Exception:
            # Two requests from a new user can race here; whoever lost simply reads the
            # row the winner wrote.
            session.rollback()
            existing = session.get(User, ctx.id)
            if existing is not None:
                return existing
            logger.warning("Could not record a shadow user row for %s", ctx.id)
        return user

    changed = False

    # Notes is the source of truth for the name, so a rename there propagates here the
    # next time the user arrives carrying a token that says so.
    if ctx.username and user.username != ctx.username:
        user.username = ctx.username
        changed = True

    if user.last_seen is None or now - user.last_seen > LAST_SEEN_REFRESH:
        user.last_seen = now
        changed = True

    if changed:
        session.add(user)
        try:
            session.commit()
            session.refresh(user)
        except Exception:
            session.rollback()
            logger.warning("Could not update the shadow user row for %s", ctx.id)

    return user
