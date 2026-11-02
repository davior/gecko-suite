"""Task routing: which model each task uses, and what a task resolves to right now."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Response, status
from sqlmodel import Session, col, select

from app.auth import CurrentUser
from app.clock import utcnow
from app.database import get_session
from app.models.ai import AIModel, AIProvider, TaskAssignment
from app.routers.models import serialise_model
from app.schemas import DataResponse, ListResponse
from app.schemas_ai import AssignmentPut, AssignmentRead, ResolveRead
from app.services import access
from app.services.registry import (
    capabilities_of,
    dump_json,
    find_assignment,
    load_json,
    resolve,
    visible_model,
)
from app.vocab import APPS, SCOPE_INSTANCE, SCOPE_USER, TASK_REQUIRES, TASKS

router = APIRouter()


def _serialise(session: Session, row: TaskAssignment, writable: bool) -> AssignmentRead:
    model = session.get(AIModel, row.model_ref)
    provider = session.get(AIProvider, model.provider_id) if model else None
    return AssignmentRead(
        id=row.id,
        scope=row.scope,
        app=row.app,
        task=row.task,
        model_ref=row.model_ref,
        model_id=model.model_id if model else "",
        model_label=(model.label or model.model_id) if model else "",
        provider_name=provider.name if provider else "",
        params=load_json(row.params, {}) or None,
        writable=writable,
    )


def _check_task_and_app(task: str, app: Optional[str]) -> None:
    if task not in TASKS:
        raise access.bad_request("bad_task", f"Unknown task: {task}")
    if app is not None and app not in APPS:
        raise access.bad_request("bad_app", f"Unknown app: {app}")


@router.get("/assignments", response_model=ListResponse[AssignmentRead])
def list_assignments(
    user: CurrentUser, scope: Optional[str] = None, session: Session = Depends(get_session)
) -> ListResponse[AssignmentRead]:
    """The caller's own routing and the instance's. Never another user's."""
    query = select(TaskAssignment).where(
        (TaskAssignment.scope == SCOPE_INSTANCE)
        | ((TaskAssignment.scope == SCOPE_USER) & (TaskAssignment.user_id == user.id))
    )
    if scope:
        query = query.where(TaskAssignment.scope == scope)
    rows = session.exec(
        query.order_by(TaskAssignment.scope, TaskAssignment.task, col(TaskAssignment.app))
    ).all()
    admin = access.caller_is_admin(session, user)
    data = [_serialise(session, r, access.can_write(r.scope, r.user_id, user, admin)) for r in rows]
    return ListResponse(data=data, total=len(data), limit=len(data), offset=0)


@router.put("/assignments", response_model=DataResponse[AssignmentRead])
def put_assignment(
    payload: AssignmentPut, user: CurrentUser, session: Session = Depends(get_session)
) -> DataResponse[AssignmentRead]:
    """Route (scope, app, task) to a model, replacing any previous choice."""
    admin = access.caller_is_admin(session, user)
    access.require_scope(payload.scope, admin)
    _check_task_and_app(payload.task, payload.app)

    model = visible_model(session, payload.model_ref, user.id)
    if model is None:
        raise access.not_found("model")
    provider = session.get(AIProvider, model.provider_id)

    # Everybody's routing may only point at everybody's providers. Otherwise an admin's
    # default would send every user's requests through the admin's personal key, and
    # the moment it resolved for anyone else it would be a model they cannot see.
    if payload.scope == SCOPE_INSTANCE and provider.scope != SCOPE_INSTANCE:
        raise access.bad_request(
            "bad_request",
            "Shared routing can only use a shared provider. Add this model to a shared "
            "provider first.",
        )

    missing = TASK_REQUIRES[payload.task] - capabilities_of(model)
    if missing:
        raise access.bad_request(
            "missing_capability",
            f"{model.label or model.model_id} cannot do {payload.task}: it needs "
            f"{', '.join(sorted(missing))}. Edit the model's capabilities if it can.",
        )

    owner = access.owner_for(payload.scope, user)
    row = find_assignment(
        session, scope=payload.scope, user_id=owner, app=payload.app, task=payload.task
    )
    if row is None:
        row = TaskAssignment(
            scope=payload.scope, user_id=owner, app=payload.app, task=payload.task, model_ref=model.id
        )
    row.model_ref = model.id
    row.params = dump_json(payload.params)
    row.updated_at = utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return DataResponse(data=_serialise(session, row, True))


@router.delete(
    "/assignments/{assignment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    response_class=Response,
)
def delete_assignment(
    assignment_id: str, user: CurrentUser, session: Session = Depends(get_session)
) -> None:
    row = session.get(TaskAssignment, assignment_id)
    if row is None or (row.scope == SCOPE_USER and row.user_id != user.id):
        raise access.not_found("assignment")
    if not access.can_write(row.scope, row.user_id, user, access.caller_is_admin(session, user)):
        raise access.admin_required()
    session.delete(row)
    session.commit()


@router.get("/resolve", response_model=DataResponse[ResolveRead])
def resolve_task(
    task: str, user: CurrentUser, app: Optional[str] = None, session: Session = Depends(get_session)
) -> DataResponse[ResolveRead]:
    """What a request for this task would use right now, and why.

    An app calls this to decide whether to offer an AI feature at all; a settings screen
    calls it to show "Notes' autotagging will use Haiku (your override)".
    """
    _check_task_and_app(task, app)
    hit = resolve(session, user.id, task, app)
    if hit is None:
        return DataResponse(data=ResolveRead(task=task, app=app))
    writable = access.can_write(
        hit.provider.scope, hit.provider.user_id, user, access.caller_is_admin(session, user)
    )
    return DataResponse(
        data=ResolveRead(
            task=task,
            app=app,
            model=serialise_model(session, hit.model, hit.provider, writable),
            matched_task=hit.task,
            rule=hit.rule,
            params=load_json(hit.assignment.params, {}) or None,
        )
    )
