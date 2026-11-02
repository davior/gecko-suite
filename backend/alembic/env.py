"""Alembic environment.

Two things worth knowing:

- The database URL comes from `app.config.settings`, not `alembic.ini`, so migrations
  and the app can never disagree about which database they mean.
- `render_as_batch=True` is required for SQLite. SQLite cannot ALTER a column or drop a
  constraint; batch mode makes Alembic rebuild the table instead, which is what lets
  this project have real migrations at all rather than append-only columns.
"""

from logging.config import fileConfig

from alembic import context
from sqlmodel import SQLModel

from app.config import settings
from app.database import engine

# Importing the package registers every table on SQLModel.metadata — autogenerate is
# blind to anything not imported by the time this runs.
import app.models  # noqa: F401

config = context.config

# Guarded so a programmatic `command.upgrade()` cannot tear down the host process's
# logging. fileConfig() replaces the root logger's handlers and, by default, disables
# every logger that already existed — under pytest that removes the capture handler and
# silences the app, so any later assertion about log output passes for the wrong reason.
# This is alembic's own documented idiom: the CLI leaves the attribute unset and still
# gets alembic.ini's logging, while an embedded caller can opt out.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.resolved_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            # Without this a column whose type changed in the models produces no
            # migration, and the drift is invisible until a query fails.
            compare_type=True,
            )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
