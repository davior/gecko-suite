"""SQLModel tables.

Every table must be imported here: Alembic's autogenerate only sees what is registered
on `SQLModel.metadata` at import time, so a table missing from this list produces an
empty migration and a silent schema drift.
"""

from app.models.ai import AIModel, AIProvider, CatalogEntry, TaskAssignment
from app.models.library import AppClient, Persona, Recipe, SystemPrompt, WebSearchProvider
from app.models.media import MediaJob
from app.models.usage import UsageEvent
from app.models.user import AuthToken, InstanceSetting, User

__all__ = [
    "AIModel", "AIProvider", "AppClient", "CatalogEntry", "MediaJob", "Persona", "Recipe", "SystemPrompt",
    "AuthToken", "InstanceSetting", "TaskAssignment", "UsageEvent", "User", "WebSearchProvider",
]
