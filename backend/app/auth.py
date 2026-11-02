"""Who is calling: the user, and from M1 on, which app on their behalf.

Copied from GVC (which copied it from GAM); read GAM's history for the reasoning. Until
identity moves here (M6), Gecko Notes is the identity provider: it signs an HS256 JWT
with a secret every app in the suite shares, and this module verifies it.

Two transports, in priority order:

1. `Authorization: Bearer <jwt>`: what the suite's own frontend sends, and what Notes,
   GAM and GVC forward when they call the suite as the signed-in user.
2. The `gecko_session` cookie Notes sets on the parent domain, so a browser already
   signed in to Notes arrives here signed in too.

The header wins when both are present, which keeps an explicit token from being
silently overridden by whatever session the browser happens to be carrying.

A third way in, an app calling on a user's behalf with no user token at all (a GAM
enrichment job that outlived the token), arrives in M1 as `X-Gecko-App` +
`X-On-Behalf-Of`, checked against registered app credentials. It is not here yet
because nothing in M0 would accept it.

Secrets at rest live in `app.crypto`, not here: unlike the siblings, the suite's
encryption key is not derived from the JWT secret.
"""

import logging
from typing import Annotated, Optional
from urllib.parse import urlsplit

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError
from pydantic import BaseModel
from sqlmodel import Session

from app.config import settings
from app.database import get_session
from app.models.user import User

logger = logging.getLogger(__name__)


class UserCtx(BaseModel):
    """Who is calling, as far as the token says.

    Deliberately thin, because gecko-notes' token is thin — `{sub, username, exp}`,
    with no `is_admin`, `iss` or `aud`. Anything richer has to be read from the local
    shadow user row or fetched from Notes, never inferred from the token.
    """

    id: str
    username: str = ""


# ─── tokens ──────────────────────────────────────────────────────────────────


def decode_token(token: str) -> dict:
    """Verify and decode, or raise JWTError. Signature and expiry are both checked."""
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "unauthorized", "message": message},
    )


def _session_not_accepted() -> HTTPException:
    """The caller *is* signed in to Notes, and this app cannot verify it.

    Distinct from `unauthorized` because the remedy is distinct, and because the
    difference is invisible from the browser: both render as "please sign in", and in
    this state signing in again returns the user to exactly the same screen. A cookie
    that carries a well-formed token whose signature does not verify means the two apps
    disagree about JWT_SECRET_KEY — an operator's problem that no amount of clicking
    fixes.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "code": "session_not_accepted",
            "message": (
                "You are signed in to Gecko Notes, but this app could not verify that "
                "session. Its JWT_SECRET_KEY probably does not match Notes'."
            ),
        },
    )


# ─── CSRF, for the cookie path only ──────────────────────────────────────────
#
# A bearer token cannot be attached by a cross-site form, an <img>, or a fetch the
# browser makes on another site's behalf — so the header path needs no protection. The
# cookie is an ambient credential and does need it.
#
# Here the write an attacker wants is "register my endpoint as everyone's provider" or
# "point this user's assistant at my server": a cross-origin form POST is a *simple*
# request, sent with no preflight. CORS would stop the attacker reading the response;
# it would not stop the write. Gecko Notes added this guard with the cookie; every app
# that accepts the same cookie does the same.

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _host_of(url: str) -> str:
    try:
        return urlsplit(url).netloc.lower()
    except ValueError:
        return ""


def _origin_allowed_for_cookie(request: Request) -> bool:
    """Whether a cookie-authenticated request may proceed.

    Fails closed on a missing Origin: a browser always sends one (or a Referer) on a
    state-changing request, so its absence means this did not come from a browser doing
    what a browser does.
    """
    if request.method.upper() in _SAFE_METHODS:
        return True

    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return False

    origin_host = _host_of(origin)
    if not origin_host:
        return False

    # Same-origin is always allowed — it is the suite's own frontend talking to its own
    # API, which is the normal deployment (nginx serves both on one origin). Comparing
    # against the Host header rather than a configured value means this needs no extra
    # setting and cannot drift from where the app is actually served.
    request_host = (request.headers.get("host") or "").lower()
    if request_host and origin_host == request_host:
        return True

    return any(origin_host == _host_of(allowed) for allowed in settings.cors_origins)


def current_user(
    request: Request,
    authorization: Annotated[Optional[str], Header()] = None,
    gecko_session: Annotated[Optional[str], Cookie()] = None,
) -> UserCtx:
    """Resolve the caller, or raise 401.

    FastAPI maps the `gecko_session` parameter to the cookie of that name; it is named
    explicitly rather than read off the request so it shows up in the OpenAPI schema
    and can be overridden in tests.
    """
    token: Optional[str] = None
    # Which transport carried it, because it changes what a failure *means*. A cookie
    # was minted by Notes moments ago and handed over by the browser; if it will not
    # verify, the two apps disagree about the secret. A header token came out of this
    # app's own localStorage and could simply be stale.
    from_cookie = False

    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
    elif gecko_session:
        token = gecko_session.strip()
        from_cookie = True
        if not _origin_allowed_for_cookie(request):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "forbidden_origin",
                    "message": "Cookie authentication requires an allowed Origin",
                },
            )

    if not token:
        # Only after both real transports have been tried, so a dev environment still
        # exercises the token path whenever a token is actually present.
        if settings.dev_auth_enabled:
            return UserCtx(id=settings.dev_auth_user, username=settings.dev_auth_user)
        raise _unauthorized("Missing bearer token or session cookie")

    try:
        payload = decode_token(token)
    except ExpiredSignatureError as exc:
        # Routine. Tokens last 30 days and then stop; nothing is misconfigured, and the
        # user fixes it by signing in again. Stays at debug so it cannot drown the
        # case below.
        logger.debug("Rejected an expired token")
        raise _unauthorized("Your session has expired. Sign in again.") from exc
    except JWTError as exc:
        # Not routine, and previously logged at debug — which is why a secret mismatch
        # presented as a silent sign-in loop with nothing in the logs at default level.
        # The commonest cause by far is the one named here, so the message carries the
        # diagnosis rather than making somebody derive it.
        logger.warning(
            "Rejected a token that did not verify (%s). If this is every request, "
            "JWT_SECRET_KEY does not match the value gecko-notes signs with.",
            exc,
        )
        raise (_session_not_accepted() if from_cookie else _unauthorized("Invalid token")) from exc

    subject = payload.get("sub")
    if not subject:
        raise _unauthorized("Token carries no subject")

    return UserCtx(id=subject, username=payload.get("username") or "")


CurrentUser = Annotated[UserCtx, Depends(current_user)]


# ─── administrators ──────────────────────────────────────────────────────────
#
# The token carries no admin claim and `User.is_admin` has no writer, so an admin is
# whoever `ADMIN_USERS` names — or a row somebody set by hand, which still counts. From
# M1, admins are who may register instance-wide providers, models and task routing.


def is_admin(user: UserCtx, record: Optional[User]) -> bool:
    """Whether this caller is an administrator.

    An entry in `ADMIN_USERS` matches an id, or a username against either the token's
    username or the shadow row's, so a configured name keeps working whichever one has
    caught up with a rename in Notes.

    Both exactly. Notes keeps usernames unique only case-sensitively and lets anyone
    rename themselves, so a case-insensitive match would make whoever renames to
    `DAVIOR` an admin alongside `davior`.
    """
    if record is not None and record.is_admin:
        return True

    usernames = {
        name for name in (user.username, record.username if record is not None else "") if name
    }
    return any(entry == user.id or entry in usernames for entry in settings.admin_user_list)


def admin_user(user: CurrentUser, session: Session = Depends(get_session)) -> UserCtx:
    """The caller, or 403 if they are not an administrator."""
    if not is_admin(user, session.get(User, user.id)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "admin_required",
                "message": "Only an administrator can change this",
            },
        )
    return user


AdminUser = Annotated[UserCtx, Depends(admin_user)]
