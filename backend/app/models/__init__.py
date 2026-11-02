"""SQLModel tables.

Every table must be imported here: Alembic's autogenerate only sees what is registered
on `SQLModel.metadata` at import time, so a table missing from this list produces an
empty migration and a silent schema drift.
"""

from app.models.ai import AIModel, AIProvider, CatalogEntry, TaskAssignment
from app.models.usage import UsageEvent
from app.models.user import User

__all__ = ["AIModel", "AIProvider", "CatalogEntry", "TaskAssignment", "UsageEvent", "User"]
