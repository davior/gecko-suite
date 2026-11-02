#!/bin/sh
# Migrations run before the app accepts traffic, not from inside it.
#
# Doing it here rather than in a startup hook means a failed migration stops the
# container instead of leaving a running app serving a schema it does not match —
# which the orchestrator can see, and a half-migrated app cannot tell you.
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Starting API..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 "$@"
