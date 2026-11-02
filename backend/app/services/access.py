"""Who may change a registry row, and the errors that say no.

Reads are decided by `app.services.registry` (instance rows and your own). Writes are
narrower: your own rows, or instance rows if you are an administrator.
"""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, status
from sqlmodel import Session

from app.auth import UserCtx, is_admin
from app.models.user import User
from app.vocab import APPS, SCOPE_INSTANCE, SCOPE_USER, SCOPES


def caller_is_admin(session: Session, user: UserCtx) -> bool:
    return is_admin(user, session.get(User, user.id))


def can_write(scope: str, owner_id: Optional[str], user: UserCtx, admin: bool) -> bool:
    if scope == SCOPE_INSTANCE:
        return admin
    return owner_id == user.id


def not_found(what: str) -> HTTPException:
    # 404 rather than 403 for another user's row: whether an id exists is not a question
    # this API answers for people who cannot see it.
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "not_found", "message": f"No such {what}"},
    )


def admin_required(message: str = "Only an administrator can change shared settings") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": "admin_required", "message": message},
    )


def bad_request(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST, detail={"code": code, "message": message}
    )


def require_scope(scope: str, admin: bool) -> None:
    """Validate a scope for a new row, and that the caller may create one there."""
    if scope not in SCOPES:
        raise bad_request("bad_scope", f"Scope must be one of: {', '.join(SCOPES)}")
    if scope == SCOPE_INSTANCE and not admin:
        raise admin_required()


def owner_for(scope: str, user: UserCtx) -> Optional[str]:
    return user.id if scope == SCOPE_USER else None


def app_for(user: UserCtx, requested: Optional[str]) -> Optional[str]:
    """The app a call is attributed to, and routed for.

    An app credential decides it. A request made with one cannot claim to be another
    app: that would let GAM's credential spend through Notes' routing and have it
    reported as Notes' spend.
    """
    if requested is not None and requested not in APPS:
        raise bad_request("bad_app", f"Unknown app: {requested}")
    if user.app and requested and requested != user.app:
        raise bad_request("bad_app", f"This credential belongs to {user.app}, not {requested}")
    return user.app or requested
