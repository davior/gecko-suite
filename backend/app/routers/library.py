"""System prompts, recipes and personas: the same scoped CRUD three times.

All three follow the registry's rule (read the shared ones and your own; write your own,
or the shared ones as an admin), so the rule is written once here as `ScopedCrud` and
each resource only says what its fields are.

No `from __future__ import annotations` in this module, deliberately: the endpoints are
defined inside `ScopedCrud.__init__` with the schema classes as closure variables, and
FastAPI resolves string annotations against the module's globals, where those names do
not exist. It would read each request body as a query parameter and answer 422.
"""

from typing import Any, Callable, Optional, Type

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field
from sqlmodel import Session, SQLModel, col, or_, select

from app.auth import CurrentUser, UserCtx
from app.clock import utcnow
from app.database import get_session
from app.models.library import Persona, Recipe, SystemPrompt
from app.schemas import DataResponse, ListResponse
from app.services import access
from app.services.registry import capabilities_of, dump_json, load_json, visible_model
from app.vocab import APPS, SCOPE_INSTANCE, SCOPE_USER, TASK_REQUIRES, TASKS

router = APIRouter()


# ─── shared ──────────────────────────────────────────────────────────────────


def visible_query(model: Type[SQLModel], user_id: str, app: Optional[str]):
    query = select(model).where(
        or_(model.scope == SCOPE_INSTANCE, (model.scope == SCOPE_USER) & (model.user_id == user_id))
    )
    if app:
        # An app sees what is for it and what is for every app.
        query = query.where(or_(col(model.app).is_(None), model.app == app))
    return query


def visible_row(session: Session, model: Type[SQLModel], row_id: str, user_id: str):
    row = session.get(model, row_id)
    if row is None or (row.scope == SCOPE_USER and row.user_id != user_id):
        return None
    return row


def _check_app(app: Optional[str]) -> None:
    if app is not None and app not in APPS:
        raise access.bad_request("bad_app", f"Unknown app: {app}")


class ScopedCrud:
    """List, create, update and delete for one scoped table."""

    def __init__(self, *, path: str, model: Type[SQLModel], name: str, read: Callable[..., BaseModel],
                 read_schema: Type[BaseModel], create_schema: Type[BaseModel],
                 update_schema: Type[BaseModel], encode: Callable[[dict], dict],
                 validate: Optional[Callable[[Session, UserCtx, dict, str], None]] = None,
                 after_write: Optional[Callable[[Session, Any], None]] = None):
        self.model, self.name, self.read = model, name, read
        self.encode, self.validate, self.after_write = encode, validate, after_write

        def writable(session: Session, user: UserCtx, row_id: str):
            row = visible_row(session, model, row_id, user.id)
            if row is None:
                raise access.not_found(name)
            if not access.can_write(row.scope, row.user_id, user, access.caller_is_admin(session, user)):
                raise access.admin_required()
            return row

        @router.get(path, response_model=ListResponse[read_schema], name=f"list_{name}s")
        def list_rows(user: CurrentUser, app: Optional[str] = None, session: Session = Depends(get_session)):
            _check_app(app)
            rows = session.exec(
                visible_query(model, user.id, app).order_by(model.scope, model.sort_order, model.created_at)
            ).all()
            admin = access.caller_is_admin(session, user)
            data = [read(r, access.can_write(r.scope, r.user_id, user, admin)) for r in rows]
            return ListResponse(data=data, total=len(data), limit=len(data), offset=0)

        @router.post(path, response_model=DataResponse[read_schema], status_code=201, name=f"create_{name}")
        def create_row(payload: create_schema, user: CurrentUser, session: Session = Depends(get_session)):  # type: ignore[valid-type]
            fields = payload.model_dump()
            scope = fields.pop("scope")
            access.require_scope(scope, access.caller_is_admin(session, user))
            _check_app(fields.get("app"))
            if validate:
                validate(session, user, fields, scope)
            row = model(scope=scope, user_id=access.owner_for(scope, user), **encode(fields))
            session.add(row)
            session.commit()
            session.refresh(row)
            if after_write:
                after_write(session, row)
                # The hook may commit, which expires the row's attributes.
                session.refresh(row)
            return DataResponse(data=read(row, True))

        @router.patch(f"{path}/{{row_id}}", response_model=DataResponse[read_schema], name=f"update_{name}")
        def update_row(row_id: str, payload: update_schema, user: CurrentUser,  # type: ignore[valid-type]
                       session: Session = Depends(get_session)):
            row = writable(session, user, row_id)
            fields = payload.model_dump(exclude_unset=True)
            if "app" in fields:
                _check_app(fields["app"])
            if validate:
                validate(session, user, fields, row.scope)
            for key, value in encode(fields).items():
                setattr(row, key, value)
            row.updated_at = utcnow()
            session.add(row)
            session.commit()
            session.refresh(row)
            if after_write:
                after_write(session, row)
                # The hook may commit, which expires the row's attributes.
                session.refresh(row)
            return DataResponse(data=read(row, True))

        @router.delete(f"{path}/{{row_id}}", status_code=status.HTTP_204_NO_CONTENT, response_model=None,
                       response_class=Response, name=f"delete_{name}")
        def delete_row(row_id: str, user: CurrentUser, session: Session = Depends(get_session)) -> None:
            session.delete(writable(session, user, row_id))
            session.commit()


# ─── system prompts ──────────────────────────────────────────────────────────


class PromptRead(BaseModel):
    id: str
    scope: str
    app: Optional[str] = None
    name: str
    content: str
    is_default: bool
    sort_order: int
    writable: bool


class PromptCreate(BaseModel):
    scope: str = "user"
    app: Optional[str] = None
    name: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=200_000)
    is_default: bool = False
    sort_order: int = 0


class PromptUpdate(BaseModel):
    app: Optional[str] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    content: Optional[str] = Field(default=None, min_length=1, max_length=200_000)
    is_default: Optional[bool] = None
    sort_order: Optional[int] = None


def _one_default(session: Session, row: SystemPrompt) -> None:
    """At most one default per (scope, owner, app), as Notes keeps one active prompt."""
    if not row.is_default:
        return
    query = select(SystemPrompt).where(
        SystemPrompt.scope == row.scope, SystemPrompt.id != row.id, col(SystemPrompt.is_default).is_(True),
        col(SystemPrompt.user_id).is_(None) if row.user_id is None else SystemPrompt.user_id == row.user_id,
        col(SystemPrompt.app).is_(None) if row.app is None else SystemPrompt.app == row.app,
    )
    for other in session.exec(query).all():
        other.is_default = False
        session.add(other)
    session.commit()


ScopedCrud(
    path="/prompts", model=SystemPrompt, name="prompt", read_schema=PromptRead,
    create_schema=PromptCreate, update_schema=PromptUpdate,
    read=lambda r, w: PromptRead(id=r.id, scope=r.scope, app=r.app, name=r.name, content=r.content,
                                 is_default=r.is_default, sort_order=r.sort_order, writable=w),
    encode=lambda f: f, after_write=_one_default,
)


# ─── recipes ─────────────────────────────────────────────────────────────────


class RecipeRead(BaseModel):
    id: str
    scope: str
    app: Optional[str] = None
    name: str
    prompt: str
    tags: list[str]
    sort_order: int
    writable: bool


class RecipeCreate(BaseModel):
    scope: str = "user"
    app: Optional[str] = None
    name: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=100_000)
    tags: list[str] = Field(default_factory=list)
    sort_order: int = 0


class RecipeUpdate(BaseModel):
    app: Optional[str] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    prompt: Optional[str] = Field(default=None, min_length=1, max_length=100_000)
    tags: Optional[list[str]] = None
    sort_order: Optional[int] = None


def _encode_recipe(fields: dict) -> dict:
    if "tags" in fields:
        fields = {**fields, "tags": dump_json(sorted(set(fields["tags"] or []))) or "[]"}
    return fields


ScopedCrud(
    path="/recipes", model=Recipe, name="recipe", read_schema=RecipeRead,
    create_schema=RecipeCreate, update_schema=RecipeUpdate,
    read=lambda r, w: RecipeRead(id=r.id, scope=r.scope, app=r.app, name=r.name, prompt=r.prompt,
                                 tags=load_json(r.tags, []), sort_order=r.sort_order, writable=w),
    encode=_encode_recipe,
)


# ─── personas ────────────────────────────────────────────────────────────────


class PersonaRead(BaseModel):
    id: str
    scope: str
    app: Optional[str] = None
    name: str
    description: str
    avatar: Optional[str] = None
    system_prompt: str
    model_overrides: dict[str, str]
    sort_order: int
    writable: bool


class PersonaCreate(BaseModel):
    scope: str = "user"
    app: Optional[str] = None
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    avatar: Optional[str] = Field(default=None, max_length=2000)
    system_prompt: str = Field(default="", max_length=200_000)
    model_overrides: dict[str, str] = Field(default_factory=dict)
    sort_order: int = 0


class PersonaUpdate(BaseModel):
    app: Optional[str] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    avatar: Optional[str] = Field(default=None, max_length=2000)
    system_prompt: Optional[str] = Field(default=None, max_length=200_000)
    model_overrides: Optional[dict[str, str]] = None
    sort_order: Optional[int] = None


def _validate_persona(session: Session, user: UserCtx, fields: dict, scope: str) -> None:
    """A persona's models must exist for the person saving it, and be able to do the
    task they are set for. A shared persona may only use shared models: otherwise it
    would route everyone through the admin's personal key."""
    from app.models.ai import AIProvider

    for task, model_ref in (fields.get("model_overrides") or {}).items():
        if task not in TASKS:
            raise access.bad_request("bad_task", f"Unknown task: {task}")
        model = visible_model(session, model_ref, user.id)
        if model is None:
            raise access.not_found("model")
        if scope == SCOPE_INSTANCE and session.get(AIProvider, model.provider_id).scope != SCOPE_INSTANCE:
            raise access.bad_request("bad_request", "A shared persona can only use shared models")
        missing = TASK_REQUIRES[task] - capabilities_of(model)
        if missing:
            raise access.bad_request("missing_capability",
                                     f"{model.label or model.model_id} cannot do {task}")


def _encode_persona(fields: dict) -> dict:
    if "model_overrides" in fields:
        fields = {**fields, "model_overrides": dump_json(fields["model_overrides"])}
    return fields


ScopedCrud(
    path="/personas", model=Persona, name="persona", read_schema=PersonaRead,
    create_schema=PersonaCreate, update_schema=PersonaUpdate,
    read=lambda r, w: PersonaRead(id=r.id, scope=r.scope, app=r.app, name=r.name, description=r.description,
                                  avatar=r.avatar, system_prompt=r.system_prompt,
                                  model_overrides=load_json(r.model_overrides, {}), sort_order=r.sort_order,
                                  writable=w),
    encode=_encode_persona, validate=_validate_persona,
)
