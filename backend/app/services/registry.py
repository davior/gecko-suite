"""Who can see which provider and model, which model a task resolves to, and its price.

Every rule that decides what a request may use lives here, so the management routers
and the gateway (M1b) cannot disagree about it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from sqlmodel import Session, col, or_, select

from app.models.ai import AIModel, AIProvider, CatalogEntry, TaskAssignment
from app.vocab import SCOPE_INSTANCE, SCOPE_USER, TASK_FALLBACK, VENDOR_OLLAMA

# ─── JSON-as-TEXT ────────────────────────────────────────────────────────────


def load_json(raw: Optional[str], default: Any) -> Any:
    """Decode a JSON-as-TEXT column, reading anything unusable as the default.

    A row written by hand or by an older version should cost one field, not the whole
    settings screen.
    """
    if not raw:
        return default
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def dump_json(value: Any) -> Optional[str]:
    """Encode for a JSON-as-TEXT column; an empty value is stored as NULL.

    `sort_keys` so the same settings always serialise to the same text: a stored value
    that changes on every save, with nothing actually changed, makes diffs and audits lie.
    """
    if value is None or value == {} or value == []:
        return None
    return json.dumps(value, sort_keys=True)


def capabilities_of(model: AIModel | CatalogEntry) -> set[str]:
    return set(load_json(model.capabilities, []))


# ─── visibility ──────────────────────────────────────────────────────────────


def visible_providers_query(user_id: str):
    """Providers a user may use: everybody's (instance) and their own.

    Never another user's. That is the one isolation rule in the registry, and every
    read goes through this query or `visible_provider` so it is written once.
    """
    return select(AIProvider).where(
        or_(
            AIProvider.scope == SCOPE_INSTANCE,
            (AIProvider.scope == SCOPE_USER) & (AIProvider.user_id == user_id),
        )
    )


def visible_provider(session: Session, provider_id: str, user_id: str) -> Optional[AIProvider]:
    provider = session.get(AIProvider, provider_id)
    if provider is None:
        return None
    if provider.scope == SCOPE_USER and provider.user_id != user_id:
        return None
    return provider


def visible_model(session: Session, model_id: str, user_id: str) -> Optional[AIModel]:
    model = session.get(AIModel, model_id)
    if model is None or visible_provider(session, model.provider_id, user_id) is None:
        return None
    return model


def usable(model: AIModel, provider: AIProvider) -> bool:
    """Whether a request could go to this model right now.

    `enabled` / `is_active` are "not this one for now" without deleting it, so a switched
    off provider or model is skipped by routing, never treated as an error.
    """
    return bool(model.is_active and provider.enabled)


# ─── resolution ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Resolved:
    model: AIModel
    provider: AIProvider
    assignment: TaskAssignment
    # The task whose assignment matched: the one asked for, or its fallback.
    task: str
    # Which of the four rules matched, for `GET /resolve` to explain itself.
    rule: str


def _candidates(user_id: str, app: Optional[str], task: str) -> list[tuple[str, dict]]:
    """The assignment lookups for one task, most specific first (D3).

    A user's own routing beats the instance's; within each, an app-specific row beats
    the all-apps one. So "Notes uses Haiku for autotagging" set by one person overrides
    the admin's suite-wide default for that person and nobody else.
    """
    rules = []
    if app:
        rules.append(("user+app", {"scope": SCOPE_USER, "user_id": user_id, "app": app}))
    rules.append(("user", {"scope": SCOPE_USER, "user_id": user_id, "app": None}))
    if app:
        rules.append(("instance+app", {"scope": SCOPE_INSTANCE, "user_id": None, "app": app}))
    rules.append(("instance", {"scope": SCOPE_INSTANCE, "user_id": None, "app": None}))
    return rules


def find_assignment(
    session: Session, *, scope: str, user_id: Optional[str], app: Optional[str], task: str
) -> Optional[TaskAssignment]:
    query = select(TaskAssignment).where(
        TaskAssignment.scope == scope, TaskAssignment.task == task
    )
    # `is_(None)` for the NULL columns: `== None` renders as `= NULL`, which matches
    # nothing in SQL, and the all-apps rows are exactly the NULL ones.
    query = query.where(
        col(TaskAssignment.user_id).is_(None) if user_id is None else TaskAssignment.user_id == user_id
    )
    query = query.where(
        col(TaskAssignment.app).is_(None) if app is None else TaskAssignment.app == app
    )
    return session.exec(query).first()


def resolve(
    session: Session, user_id: str, task: str, app: Optional[str] = None
) -> Optional[Resolved]:
    """The model this user's request for `task` goes to, or None if nothing is routed.

    None rather than an error, as with the siblings' `build_provider`: an app with
    nothing configured for a task has no AI for it, and everything else still works.

    The task's own four rules are tried before its fallback's, so a user's
    "chat.assistant" never overrides the instance's explicit "chat.draft". An assignment
    whose model or provider is switched off is skipped, so turning one off falls through
    to the next rule instead of breaking the task.
    """
    for candidate_task in (task, TASK_FALLBACK.get(task)):
        if candidate_task is None:
            continue
        for rule, where in _candidates(user_id, app, candidate_task):
            assignment = find_assignment(session, task=candidate_task, **where)
            if assignment is None:
                continue
            model = session.get(AIModel, assignment.model_ref)
            if model is None:
                continue
            # Re-checked at read time, not only on write: an instance assignment can
            # point only at an instance model, but a user's assignment is checked
            # against *this* user, which is the guarantee that matters.
            provider = visible_provider(session, model.provider_id, user_id)
            if provider is None or not usable(model, provider):
                continue
            return Resolved(model, provider, assignment, candidate_task, rule)
    return None


# ─── pricing ─────────────────────────────────────────────────────────────────


def catalog_entry_for(session: Session, vendor: str, model_id: str) -> Optional[CatalogEntry]:
    """The catalogue row for a model: an exact id, else the longest matching family.

    Family rows end in "*" ("claude-opus-*"). The longest prefix wins, so
    "claude-opus-5-*" beats "claude-opus-*" for claude-opus-5-5, whatever order the
    migrations happened to insert them in.
    """
    entries = session.exec(select(CatalogEntry).where(CatalogEntry.vendor == vendor)).all()
    wanted = (model_id or "").lower()
    best: Optional[CatalogEntry] = None
    best_len = -1
    for entry in entries:
        pattern = entry.model_id.lower()
        if pattern == wanted:
            return entry
        if pattern.endswith("*") and wanted.startswith(pattern[:-1]):
            if len(pattern) > best_len:
                best, best_len = entry, len(pattern)
    return best


def effective_pricing(session: Session, model: AIModel, provider: AIProvider) -> Optional[dict]:
    """The price used to estimate a call through this model, or None if unknown.

    The model's own price wins; otherwise the catalogue's. Ollama is free, and says so
    with zeros: it runs on the user's own hardware. Everything else unknown is None,
    never zero, because a stored 0.0 claims "this was free".
    """
    own = load_json(model.pricing, {})
    if own:
        return own
    if provider.vendor == VENDOR_OLLAMA:
        return {"currency": "USD", "input": 0.0, "output": 0.0}
    entry = catalog_entry_for(session, provider.vendor, model.model_id)
    if entry is None:
        return None
    return load_json(entry.pricing, {}) or None
