"""Application entry point.

Auth is a dependency (`app.auth.CurrentUser`), not a middleware, so there is no
public-path allowlist here to keep in step with the routers — a route is protected
exactly when it declares that it is.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlmodel import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.auth import CurrentUser, is_admin
from app.config import settings
from app.database import engine, get_session
from app.schemas import ClientConfig, DataResponse, HealthResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # One line here beats the same fact arriving as a traceback on whichever request
    # first touches a table that does not exist yet.
    from app.schema_check import log_if_behind

    log_if_behind(engine)
    yield


app = FastAPI(title="Gecko Suite", version=VERSION, lifespan=lifespan)

# Behind Caddy/nginx, so X-Forwarded-* decides scheme and client address.
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

if not settings.cors_origins:
    logger.warning("CORS_ORIGIN is not set — cross-origin API requests will be blocked.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(request, exc: StarletteHTTPException):
    """Normalise every error to `{"detail": {"code", "message"}}`.

    FastAPI's own handler passes `detail` straight through, so a raise with a plain
    string would produce a different shape from one raised with a dict.
    """
    detail = exc.detail
    if not isinstance(detail, dict) or "code" not in detail:
        detail = {"code": _default_code(exc.status_code), "message": str(detail)}
    return JSONResponse(status_code=exc.status_code, content={"detail": detail})


def _default_code(status_code: int) -> str:
    return {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        413: "too_large",
        422: "unprocessable",
    }.get(status_code, "error")


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Unauthenticated on purpose — the container healthcheck calls it."""
    return HealthResponse(status="ok", version=VERSION)


@app.get("/api/config", response_model=DataResponse[ClientConfig])
def client_config() -> DataResponse[ClientConfig]:
    """What the browser needs before anybody is signed in: where to sign in.

    Served at runtime rather than compiled into the bundle, so pointing a running
    container at a different Notes is a restart, not a rebuild (GAM learnt this one).
    """
    return DataResponse(data=ClientConfig(notes_base_url=settings.notes_base_url))


@app.get("/api/me", response_model=DataResponse[dict])
def me(user: CurrentUser, session: Session = Depends(get_session)) -> DataResponse[dict]:
    """Who is signed in, and the record that they were here.

    The shadow row is written here because this is the one call the frontend makes
    exactly once per session. `is_admin` decides whether the frontend offers the
    instance-wide settings (shared providers, default routing) at all.
    """
    from app.services.users import ensure_user

    record = ensure_user(session, user)
    return DataResponse(
        data={
            "id": record.id,
            "username": record.username or user.username,
            "is_admin": is_admin(user, record),
            "first_seen": record.created_at.isoformat() if record.created_at else None,
        }
    )
