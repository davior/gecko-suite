"""The credentials apps use to call the suite on a user's behalf. Admin only."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlmodel import Session

from app.auth import AdminUser
from app.clock import utcnow
from app.database import get_session
from app.models.library import AppClient
from app.schemas import DataResponse, ListResponse
from app.services import access
from app.vocab import APPS

router = APIRouter()


class AppClientRead(BaseModel):
    app: str
    issued: bool
    enabled: bool = False
    created_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None


class IssuedSecret(BaseModel):
    app: str
    # Shown once. Put it in the app's .env as SUITE_APP_SECRET; it cannot be read back.
    secret: str


def _check(app: str) -> None:
    if app not in APPS:
        raise access.bad_request("bad_app", f"Unknown app: {app}")


@router.get("", response_model=ListResponse[AppClientRead])
def list_apps(_: AdminUser, session: Session = Depends(get_session)):
    data = []
    for app in APPS:
        row = session.get(AppClient, app)
        data.append(AppClientRead(app=app, issued=row is not None, enabled=bool(row and row.enabled),
                                  created_at=row.created_at if row else None,
                                  last_used_at=row.last_used_at if row else None))
    return ListResponse(data=data, total=len(data), limit=len(data), offset=0)


@router.post("/{app}/secret", response_model=DataResponse[IssuedSecret], status_code=201)
def issue_secret(app: str, _: AdminUser, session: Session = Depends(get_session)):
    """Issue a new secret for an app, replacing any previous one at once.

    Replacing rather than adding: an app has one credential, so a leaked one is revoked by
    issuing a new one, and there is no list of old secrets to forget to clean up.
    """
    _check(app)
    secret = secrets.token_urlsafe(32)
    row = session.get(AppClient, app) or AppClient(app=app, secret_hash="")
    row.secret_hash = hashlib.sha256(secret.encode()).hexdigest()
    row.enabled = True
    row.created_at = utcnow()
    row.last_used_at = None
    session.add(row)
    session.commit()
    return DataResponse(data=IssuedSecret(app=app, secret=secret))


@router.delete("/{app}", status_code=status.HTTP_204_NO_CONTENT, response_model=None, response_class=Response)
def revoke(app: str, _: AdminUser, session: Session = Depends(get_session)) -> None:
    _check(app)
    row = session.get(AppClient, app)
    if row is not None:
        session.delete(row)
        session.commit()
