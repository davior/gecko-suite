"""The one place this app asks what time it is.

Every timestamp here is naive UTC — no tzinfo — because that is what the columns hold
and what every comparison in the codebase assumes. `datetime.utcnow()` returned exactly
that, but it is deprecated from Python 3.12 and scheduled for removal, so it lives on
here as one function rather than as fourteen call sites that each have to be found again
when it goes.

The `.replace(tzinfo=None)` is the load-bearing part, not tidying. `datetime.now(utc)` is
*aware*, so substituting it directly would not be a rename: `jobs.runner.is_stale`
subtracts this from a value loaded out of the database, and mixing aware with naive
raises TypeError rather than quietly returning the wrong answer. Storing aware datetimes
instead is a defensible design, but it is a migration — every existing row is naive — and
not something a deprecation warning should be allowed to decide.
"""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Now, in UTC, with no tzinfo attached."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
