"""Accounts: the sign-in, registration, two-factor and recovery routes (M6).

Ported from gecko-notes' `routers/auth.py`, route for route, with the suite's envelopes:
`{"data": ...}` answers and `{"detail": {"code", "message"}}` errors. The messages are
Notes' own, and so are the limits: 5 sign-ins a minute, 10 second steps, 3 recovery emails.

What a session is does not change (see `app/accounts`): the token and the cookie are the
ones Notes issued, so signing in here signs a person in to Notes, GAM and GVC as well.
"""

# No `from __future__ import annotations` here, deliberately: slowapi wraps the rate-limited
# routes, and FastAPI then resolves string annotations in slowapi's module, not this one, so
# every body and dependency would be read as a query parameter (a 422 on every sign-in).

from datetime import timedelta
from typing import Literal, Optional, Union

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from jose import JWTError
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import func
from sqlmodel import Session, select

from app import crypto
from app.accounts import instance, mail, passwords, tokens, twofa
from app.auth import CurrentUser, is_admin
from app.clock import utcnow
from app.config import settings
from app.database import get_session
from app.limiter import limiter
from app.models.user import AuthToken, User, new_id
from app.schemas import DataResponse

router = APIRouter()

VERIFY_HOURS = 24
RESET_MINUTES = 60
EMAIL_CODE_MINUTES = 10
EMAIL_CODE_MAX_ATTEMPTS = 5


# ─── shapes ──────────────────────────────────────────────────────────────────


class AccountRead(BaseModel):
    id: str
    username: str
    email: Optional[str] = None
    is_active: bool
    is_admin: bool
    email_verified: bool
    two_factor_method: Optional[str] = None
    created_at: Optional[str] = None
    last_login: Optional[str] = None


class Session_(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AccountRead


class TwoFactorRequired(BaseModel):
    two_factor_required: Literal[True] = True
    method: str
    challenge_token: str


class Register(BaseModel):
    username: str
    email: EmailStr
    password: str

    @field_validator("username")
    @classmethod
    def _name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("A username is required")
        return value

    @field_validator("password")
    @classmethod
    def _strong(cls, value: str) -> str:
        return passwords.check_strength(value)


class Login(BaseModel):
    username: str
    password: str


class SecondStep(BaseModel):
    challenge_token: str
    code: str


class TokenIn(BaseModel):
    token: str


class EmailIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _strong(cls, value: str) -> str:
        return passwords.check_strength(value)


class ProfileIn(BaseModel):
    username: Optional[str] = None
    email: Optional[EmailStr] = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _strong(cls, value: str) -> str:
        return passwords.check_strength(value)


class TwoFactorStatus(BaseModel):
    enabled: bool
    method: Optional[str] = None
    email_available: bool


class TotpSetup(BaseModel):
    secret: str
    otpauth_uri: str
    qr_data_uri: str


class TotpEnable(BaseModel):
    secret: str
    code: str


class CodeIn(BaseModel):
    code: str


class PasswordIn(BaseModel):
    password: str


class Message(BaseModel):
    message: str


def _iso(value) -> Optional[str]:
    return value.isoformat() if value else None


def read(user: User, *, admin: Optional[bool] = None) -> AccountRead:
    return AccountRead(
        id=user.id, username=user.username, email=user.email, is_active=user.is_active,
        is_admin=user.is_admin if admin is None else admin, email_verified=user.email_verified,
        two_factor_method=user.two_factor_method, created_at=_iso(user.created_at),
        last_login=_iso(user.last_login))


def _error(code: int, slug: str, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail={"code": slug, "message": message})


# ─── helpers, as Notes has them ──────────────────────────────────────────────


def by_username(session: Session, username: str) -> Optional[User]:
    return session.exec(select(User).where(User.username == username, User.hashed_password != None)).first()  # noqa: E711


def by_email(session: Session, email: str) -> Optional[User]:
    return session.exec(select(User).where(func.lower(User.email) == email.strip().lower(),
                                           User.hashed_password != None)).first()  # noqa: E711


def _account(session: Session, user) -> User:
    record = session.get(User, user.id)
    if record is None or not record.hashed_password:
        # Signed in through a Notes token, but no account here yet (the importer has not
        # brought it across). Nothing here can be changed for them.
        raise _error(404, "no_account", "There is no Gecko Suite account for this session yet.")
    return record


def finish_sign_in(session: Session, user: User, response: Response) -> Session_:
    user.last_login = utcnow()
    session.add(user)
    session.commit()
    session.refresh(user)
    token = tokens.session_token(user)
    response.set_cookie(
        settings.auth_cookie_name, token, max_age=settings.access_token_days * 24 * 3600,
        httponly=True, secure=settings.auth_cookie_secure, samesite="lax",
        domain=settings.auth_cookie_domain or None, path="/")
    return Session_(access_token=token, user=read(user))


def _new_secret(session: Session, user_id: str, purpose: str, raw: str, lifetime: timedelta) -> None:
    for row in session.exec(select(AuthToken).where(AuthToken.user_id == user_id, AuthToken.purpose == purpose,
                                                    AuthToken.used_at == None)).all():  # noqa: E711
        row.used_at = utcnow()
        session.add(row)
    session.add(AuthToken(id=new_id(), user_id=user_id, purpose=purpose, token_hash=tokens.hash_secret(raw),
                          expires_at=utcnow() + lifetime))
    session.commit()


def _spend_link(session: Session, purpose: str, raw: str) -> Optional[AuthToken]:
    row = session.exec(select(AuthToken).where(AuthToken.purpose == purpose,
                                               AuthToken.token_hash == tokens.hash_secret(raw),
                                               AuthToken.used_at == None)).first()  # noqa: E711
    return row if row and row.expires_at >= utcnow() else None


def _send_verification(session: Session, background: BackgroundTasks, user: User) -> None:
    raw = tokens.url_token()
    _new_secret(session, user.id, "verify_email", raw, timedelta(hours=VERIFY_HOURS))
    mail.send(user.email, "welcome_verify.md", f"Verify your {mail.APP_NAME} email",
              {"username": user.username, "verify_url": f"{mail.base_url()}/verify-email?token={raw}",
               "expires_hours": VERIFY_HOURS}, background)


def _send_reset(session: Session, background: BackgroundTasks, user: User) -> None:
    raw = tokens.url_token()
    _new_secret(session, user.id, "password_reset", raw, timedelta(minutes=RESET_MINUTES))
    mail.send(user.email, "password_reset.md", f"Reset your {mail.APP_NAME} password",
              {"username": user.username, "reset_url": f"{mail.base_url()}/reset-password?token={raw}",
               "expires_minutes": RESET_MINUTES}, background)


def _send_code(session: Session, background: BackgroundTasks, user: User) -> None:
    code = tokens.numeric_code()
    _new_secret(session, user.id, "twofa_email", code, timedelta(minutes=EMAIL_CODE_MINUTES))
    mail.send(user.email, "twofa_code.md", f"Your {mail.APP_NAME} sign-in code",
              {"username": user.username, "code": code, "expires_minutes": EMAIL_CODE_MINUTES}, background)


def _spend_code(session: Session, user: User, code: str) -> bool:
    row = session.exec(select(AuthToken).where(AuthToken.user_id == user.id, AuthToken.purpose == "twofa_email",
                                               AuthToken.used_at == None)  # noqa: E711
                       .order_by(AuthToken.created_at.desc())).first()
    if not row or row.expires_at < utcnow():
        return False
    if row.attempts >= EMAIL_CODE_MAX_ATTEMPTS:
        row.used_at = utcnow()
    elif tokens.hash_secret(code) == row.token_hash:
        row.used_at = utcnow()
        session.add(row)
        session.commit()
        return True
    else:
        row.attempts += 1
    session.add(row)
    session.commit()
    return False


def _totp_secret(user: User) -> str:
    try:
        return crypto.decrypt(user.totp_secret) if user.totp_secret else ""
    except Exception:  # noqa: BLE001 - an unreadable secret is a failed code, said as one
        return ""


# ─── registration and sign-in ────────────────────────────────────────────────


@router.post("/register", response_model=DataResponse[AccountRead], status_code=201)
def register(payload: Register, background: BackgroundTasks, session: Session = Depends(get_session)):
    if by_username(session, payload.username):
        raise _error(400, "username_taken", "Username already taken")
    if by_email(session, payload.email):
        raise _error(400, "email_taken", "Email already registered")
    accounts = session.exec(select(func.count()).select_from(User)
                            .where(User.hashed_password != None)).one()  # noqa: E711
    first = accounts == 0
    # The first account is the administrator, and is never blocked by the sign-up switch:
    # there would be nobody to turn it back on.
    if not first and not instance.get(session, instance.REGISTRATION_ENABLED):
        raise _error(403, "registration_closed", "New registrations are currently disabled")
    verified = True if first else not instance.verification_required(session)
    user = User(id=new_id(), username=payload.username, email=str(payload.email),
                hashed_password=passwords.hash_password(payload.password), is_admin=first,
                email_verified=verified, created_at=utcnow())
    session.add(user)
    session.commit()
    session.refresh(user)
    if not verified:
        _send_verification(session, background, user)
    return DataResponse(data=read(user))


@router.post("/login", response_model=DataResponse[Union[Session_, TwoFactorRequired]])
@limiter.limit("5/minute")
def login(request: Request, payload: Login, background: BackgroundTasks, response: Response,
          session: Session = Depends(get_session)):
    user = by_username(session, payload.username)
    if not user or not passwords.verify_password(payload.password, user.hashed_password):
        raise _error(401, "invalid_credentials", "Invalid credentials")
    if not user.is_active:
        raise _error(403, "account_disabled", "Account disabled")
    if not user.email_verified and instance.verification_required(session):
        raise _error(403, "email_not_verified", "Please verify your email address before signing in.")
    if user.two_factor_method:
        if user.two_factor_method == "email":
            _send_code(session, background, user)
        return DataResponse(data=TwoFactorRequired(method=user.two_factor_method,
                                                   challenge_token=tokens.challenge_token(user.id, user.two_factor_method)))
    return DataResponse(data=finish_sign_in(session, user, response))


@router.post("/login/2fa", response_model=DataResponse[Session_])
@limiter.limit("10/minute")
def login_second_step(request: Request, payload: SecondStep, response: Response,
                      session: Session = Depends(get_session)):
    try:
        claims = tokens.read_challenge(payload.challenge_token)
    except JWTError:
        raise _error(401, "challenge_invalid", "Invalid or expired 2FA challenge")
    user = session.get(User, claims.get("sub"))
    if not user or not user.two_factor_method or not user.is_active:
        raise _error(401, "challenge_invalid", "Invalid or expired 2FA challenge")
    ok = (twofa.verify_code(_totp_secret(user), payload.code) if user.two_factor_method == "totp"
          else _spend_code(session, user, payload.code))
    if not ok:
        raise _error(401, "code_invalid", "Invalid verification code")
    return DataResponse(data=finish_sign_in(session, user, response))


@router.post("/logout", status_code=204)
def logout(response: Response):
    """Clears the shared cookie, for every app at once. Needs no session: signing out has
    to work with an expired one."""
    response.delete_cookie(settings.auth_cookie_name, domain=settings.auth_cookie_domain or None,
                           path="/", samesite="lax")


# ─── verification and recovery ───────────────────────────────────────────────


@router.post("/verify-email", response_model=DataResponse[Message])
def verify_email(payload: TokenIn, session: Session = Depends(get_session)):
    row = _spend_link(session, "verify_email", payload.token)
    user = session.get(User, row.user_id) if row else None
    if not user:
        raise _error(400, "link_invalid", "Invalid or expired verification link")
    user.email_verified, row.used_at = True, utcnow()
    session.add_all([user, row])
    session.commit()
    return DataResponse(data=Message(message="Your email has been verified. You can now sign in."))


@router.post("/resend-verification", response_model=DataResponse[Message])
@limiter.limit("3/minute")
def resend_verification(request: Request, payload: EmailIn, background: BackgroundTasks,
                        session: Session = Depends(get_session)):
    user = by_email(session, payload.email)
    if user and not user.email_verified and instance.verification_required(session):
        _send_verification(session, background, user)
    # The same answer either way: whether an address has an account is not for a stranger.
    return DataResponse(data=Message(message="If that account exists and needs verification, a new email is on its way."))


@router.post("/forgot-password", response_model=DataResponse[Message])
@limiter.limit("3/minute")
def forgot_password(request: Request, payload: EmailIn, background: BackgroundTasks,
                    session: Session = Depends(get_session)):
    user = by_email(session, payload.email)
    if user and settings.email_enabled:
        _send_reset(session, background, user)
    return DataResponse(data=Message(message="If that email is registered, a password reset link has been sent."))


@router.post("/reset-password", response_model=DataResponse[Message])
@limiter.limit("5/minute")
def reset_password(request: Request, payload: ResetIn, session: Session = Depends(get_session)):
    row = _spend_link(session, "password_reset", payload.token)
    user = session.get(User, row.user_id) if row else None
    if not user:
        raise _error(400, "link_invalid", "Invalid or expired reset link")
    user.hashed_password = passwords.hash_password(payload.new_password)
    user.email_verified = True  # controlling the inbox proves the address too
    row.used_at = utcnow()
    session.add_all([user, row])
    session.commit()
    return DataResponse(data=Message(message="Your password has been updated. You can now sign in."))


# ─── the signed-in account ───────────────────────────────────────────────────


@router.get("/me", response_model=DataResponse[AccountRead])
def me(user: CurrentUser, session: Session = Depends(get_session)):
    record = _account(session, user)
    return DataResponse(data=read(record, admin=is_admin(user, record)))


@router.get("/session", response_model=DataResponse[Session_])
def current_session(user: CurrentUser, session: Session = Depends(get_session)):
    """Trade a session (cookie or header) for a fresh token and the account. A fresh one,
    not the one presented, so the caller's copy has its own full lifetime."""
    record = _account(session, user)
    return DataResponse(data=Session_(access_token=tokens.session_token(record), user=read(record)))


@router.patch("/me", response_model=DataResponse[AccountRead])
def update_me(payload: ProfileIn, user: CurrentUser, background: BackgroundTasks,
              session: Session = Depends(get_session)):
    record = _account(session, user)
    if payload.username is not None and payload.username.strip() and payload.username.strip() != record.username:
        if by_username(session, payload.username.strip()):
            raise _error(400, "username_taken", "Username already taken")
        record.username = payload.username.strip()
    changed = payload.email is not None and str(payload.email).lower() != (record.email or "").lower()
    if changed:
        if by_email(session, str(payload.email)):
            raise _error(400, "email_taken", "Email already registered")
        record.email = str(payload.email)
        if instance.verification_required(session):
            record.email_verified = False
    session.add(record)
    session.commit()
    session.refresh(record)
    if changed and not record.email_verified:
        _send_verification(session, background, record)
    return DataResponse(data=read(record))


@router.post("/me/change-password", status_code=204)
def change_password(payload: PasswordChange, user: CurrentUser, session: Session = Depends(get_session)):
    record = _account(session, user)
    if not passwords.verify_password(payload.current_password, record.hashed_password):
        raise _error(400, "password_incorrect", "Current password is incorrect")
    record.hashed_password = passwords.hash_password(payload.new_password)
    session.add(record)
    session.commit()


# ─── two-factor ──────────────────────────────────────────────────────────────


def _status(user: User) -> TwoFactorStatus:
    return TwoFactorStatus(enabled=bool(user.two_factor_method), method=user.two_factor_method,
                           email_available=settings.email_enabled)


@router.get("/2fa/status", response_model=DataResponse[TwoFactorStatus])
def two_factor_status(user: CurrentUser, session: Session = Depends(get_session)):
    return DataResponse(data=_status(_account(session, user)))


@router.post("/2fa/totp/setup", response_model=DataResponse[TotpSetup])
def totp_setup(user: CurrentUser, session: Session = Depends(get_session)):
    """A fresh secret and its QR. Nothing is saved until a code from it is confirmed, so an
    abandoned setup cannot replace a working authenticator."""
    record = _account(session, user)
    secret = twofa.generate_secret()
    uri = twofa.provisioning_uri(secret, record.email or record.username)
    return DataResponse(data=TotpSetup(secret=secret, otpauth_uri=uri, qr_data_uri=twofa.qr_data_uri(uri)))


@router.post("/2fa/totp/enable", response_model=DataResponse[TwoFactorStatus])
def totp_enable(payload: TotpEnable, user: CurrentUser, session: Session = Depends(get_session)):
    record = _account(session, user)
    if not twofa.verify_code(payload.secret, payload.code):
        raise _error(400, "code_invalid", "That code didn't match. Try again.")
    record.totp_secret, record.two_factor_method = crypto.encrypt(payload.secret), "totp"
    session.add(record)
    session.commit()
    return DataResponse(data=_status(record))


@router.post("/2fa/email/setup", response_model=DataResponse[Message])
def email_two_factor_setup(user: CurrentUser, background: BackgroundTasks, session: Session = Depends(get_session)):
    record = _account(session, user)
    if not settings.email_enabled:
        raise _error(400, "email_unavailable", "Email 2FA is unavailable because email isn't configured.")
    _send_code(session, background, record)
    return DataResponse(data=Message(message="We've emailed you a code to confirm email-based 2FA."))


@router.post("/2fa/email/verify", response_model=DataResponse[TwoFactorStatus])
def email_two_factor_verify(payload: CodeIn, user: CurrentUser, session: Session = Depends(get_session)):
    record = _account(session, user)
    if not _spend_code(session, record, payload.code):
        raise _error(400, "code_invalid", "That code didn't match or has expired.")
    record.two_factor_method = "email"
    session.add(record)
    session.commit()
    return DataResponse(data=_status(record))


@router.post("/2fa/disable", response_model=DataResponse[TwoFactorStatus])
def two_factor_disable(payload: PasswordIn, user: CurrentUser, session: Session = Depends(get_session)):
    record = _account(session, user)
    if not passwords.verify_password(payload.password, record.hashed_password):
        raise _error(400, "password_incorrect", "Password is incorrect")
    record.two_factor_method = record.totp_secret = None
    session.add(record)
    session.commit()
    return DataResponse(data=_status(record))
