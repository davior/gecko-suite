"""Account administration (M6): the Users screen. Ported from gecko-notes' `routers/users.py`
and its two sign-up switches; Notes keeps the views of its own data (storage, metrics).

Deleting an account removes the account and its outstanding links and codes, as Notes'
did. Nothing the person made anywhere is deleted (their notes stay in Notes, their own
providers and usage here), and their id is never reused, so nothing is reattached to
someone else.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy import func, or_
from sqlmodel import Session, col, select

from app.accounts import instance, passwords
from app.auth import AdminUser
from app.database import get_session
from app.models.user import AuthToken, User
from app.routers.accounts import AccountRead, read
from app.schemas import DataResponse, ListResponse

router = APIRouter()


class AccountUpdate(BaseModel):
    is_active: Optional[bool] = None
    is_admin: Optional[bool] = None


class PasswordSet(BaseModel):
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _strong(cls, value: str) -> str:
        return passwords.check_strength(value)


class SignUpSettings(BaseModel):
    registration_enabled: bool
    email_verification_required: bool


class SignUpSettingsUpdate(BaseModel):
    registration_enabled: Optional[bool] = None
    email_verification_required: Optional[bool] = None


def _error(code: int, slug: str, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail={"code": slug, "message": message})


def _get(session: Session, user_id: str) -> User:
    user = session.get(User, user_id)
    if user is None or not user.hashed_password:
        raise _error(404, "not_found", "Account not found")
    return user


def _admins_left(session: Session, excluding: str) -> int:
    return session.exec(select(func.count()).select_from(User).where(
        col(User.is_admin).is_(True), col(User.is_active).is_(True), User.hashed_password != None,  # noqa: E711
        User.id != excluding)).one()


@router.get("/users", response_model=ListResponse[AccountRead])
def list_accounts(admin: AdminUser, session: Session = Depends(get_session),
                  q: Optional[str] = None, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    query = select(User).where(User.hashed_password != None)  # noqa: E711 - shadows are not accounts
    if q:
        like = f"%{q.strip().lower()}%"
        query = query.where(or_(func.lower(User.username).like(like), func.lower(User.email).like(like)))
    total = session.exec(select(func.count()).select_from(query.subquery())).one()
    rows = session.exec(query.order_by(col(User.created_at)).offset(offset).limit(limit)).all()
    return ListResponse(data=[read(u) for u in rows], total=total, limit=limit, offset=offset)


@router.patch("/users/{user_id}", response_model=DataResponse[AccountRead])
def update_account(user_id: str, payload: AccountUpdate, admin: AdminUser, session: Session = Depends(get_session)):
    user = _get(session, user_id)
    losing_admin = (payload.is_admin is False and user.is_admin) or (payload.is_active is False and user.is_admin)
    if losing_admin and _admins_left(session, excluding=user.id) == 0:
        # Without one, nobody could reach this screen again to undo it.
        raise _error(400, "last_admin", "This is the last administrator. Make someone else one first.")
    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.is_admin is not None:
        user.is_admin = payload.is_admin
    session.add(user)
    session.commit()
    session.refresh(user)
    return DataResponse(data=read(user))


@router.post("/users/{user_id}/reset-password", status_code=204)
def set_password(user_id: str, payload: PasswordSet, admin: AdminUser, session: Session = Depends(get_session)):
    user = _get(session, user_id)
    user.hashed_password = passwords.hash_password(payload.new_password)
    session.add(user)
    session.commit()


@router.delete("/users/{user_id}", status_code=204)
def delete_account(user_id: str, admin: AdminUser, session: Session = Depends(get_session)):
    if user_id == admin.id:
        raise _error(400, "own_account", "Cannot delete your own account")
    user = _get(session, user_id)
    if user.is_admin and _admins_left(session, excluding=user.id) == 0:
        raise _error(400, "last_admin", "This is the last administrator. Make someone else one first.")
    for row in session.exec(select(AuthToken).where(AuthToken.user_id == user_id)).all():
        session.delete(row)
    session.delete(user)
    session.commit()


@router.get("/settings", response_model=DataResponse[SignUpSettings])
def get_settings(admin: AdminUser, session: Session = Depends(get_session)):
    return DataResponse(data=SignUpSettings(
        registration_enabled=bool(instance.get(session, instance.REGISTRATION_ENABLED)),
        email_verification_required=bool(instance.get(session, instance.EMAIL_VERIFICATION_REQUIRED))))


@router.put("/settings", response_model=DataResponse[SignUpSettings])
def put_settings(payload: SignUpSettingsUpdate, admin: AdminUser, session: Session = Depends(get_session)):
    if payload.registration_enabled is not None:
        instance.put(session, instance.REGISTRATION_ENABLED, payload.registration_enabled)
    if payload.email_verification_required is not None:
        instance.put(session, instance.EMAIL_VERIFICATION_REQUIRED, payload.email_verification_required)
    session.commit()
    return get_settings(admin, session)
