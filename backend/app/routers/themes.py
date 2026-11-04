"""Themes and which one each app wears (M7).

The library follows the registry's scope rule (read the shared ones and your own; write
your own, or the shared ones as an admin). The selection is a preference per app, so
Notes, GAM, GVC and the suite can each wear a different theme; nothing is chosen until
the person chooses, and then the app shows its plain light or dark as it always did.

The apps' pages call these routes directly, cross-origin, with the shared session: the
suite's CORS and its cookie origin check both read `CORS_ORIGIN`, which therefore lists
the apps' addresses.

Background images are stored here and served from a public path, because a CSS `url()`
sends no credentials and may be drawn by any of the four apps. The file names are
random, so an image is only reachable by someone given the theme that uses it.
"""

import json
import re
import secrets
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlmodel import Session, or_, select

from app.auth import CurrentUser
from app.clock import utcnow
from app.config import settings
from app.database import get_session
from app.models.theme import Theme, UserPreference
from app.schemas import DataResponse, ListResponse
from app.services import access
from app.vocab import APPS, SCOPE_INSTANCE, SCOPE_USER

router = APIRouter()
# Mounted at /api, not /api/v1: an image URL is baked into themes, so it must not move
# with the API's version.
public_router = APIRouter()

# Where a theme can be chosen: the three apps and the suite's own pages.
THEMED_APPS = (*APPS, "suite")
IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}
MAX_IMAGE_BYTES = 8 * 1024 * 1024
_COLOR = re.compile(r"^#[0-9a-fA-F]{3,8}$")
_IMAGE_NAME = re.compile(r"^[A-Za-z0-9_-]{16,64}\.(png|jpg|webp|gif)$")


def preference_key(app: str) -> str:
    return f"theme.{app}"


# ─── shapes ──────────────────────────────────────────────────────────────────


def _check_color(value: Optional[str]) -> Optional[str]:
    if value is None or value == "":
        return None
    if not _COLOR.match(value):
        raise ValueError("A colour is #rgb, #rrggbb or #rrggbbaa")
    return value


def _check_image(value: Optional[str]) -> Optional[str]:
    # It ends up inside a CSS `url(...)` on four sites: an absolute http(s) address with
    # nothing that could close the url() or start another declaration.
    if not value:
        return None
    if not re.match(r"^https?://[^\s'\"()\\;<>]+$", value):
        raise ValueError("A background image must be an http(s) address")
    return value


class ThemeFields(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    mode: Literal["light", "dark"] = "light"
    bg_type: Literal["flat", "gradient", "image"] = "flat"
    bg_color1: str = "#f0f4ff"
    bg_color2: Optional[str] = None
    bg_image_url: Optional[str] = None
    bg_image_mode: Literal["fill", "stretch", "repeat"] = "fill"
    bg_blur: float = Field(0.0, ge=0, le=50)
    glass_opacity: float = Field(0.30, ge=0, le=1)
    glass_blur: float = Field(12.0, ge=0, le=60)
    shadow_size: float = Field(8.0, ge=0, le=60)
    shadow_blur: float = Field(16.0, ge=0, le=120)

    @field_validator("bg_color1", "bg_color2")
    @classmethod
    def _color(cls, value: Optional[str]) -> Optional[str]:
        return _check_color(value)

    @field_validator("bg_image_url")
    @classmethod
    def _image(cls, value: Optional[str]) -> Optional[str]:
        return _check_image(value)


class ThemeCreate(ThemeFields):
    scope: Literal["user", "instance"] = SCOPE_USER


class ThemeUpdate(BaseModel):
    """Every field optional; unset ones keep their value."""

    name: Optional[str] = Field(None, min_length=1, max_length=80)
    mode: Optional[Literal["light", "dark"]] = None
    bg_type: Optional[Literal["flat", "gradient", "image"]] = None
    bg_color1: Optional[str] = None
    bg_color2: Optional[str] = None
    bg_image_url: Optional[str] = None
    bg_image_mode: Optional[Literal["fill", "stretch", "repeat"]] = None
    bg_blur: Optional[float] = Field(None, ge=0, le=50)
    glass_opacity: Optional[float] = Field(None, ge=0, le=1)
    glass_blur: Optional[float] = Field(None, ge=0, le=60)
    shadow_size: Optional[float] = Field(None, ge=0, le=60)
    shadow_blur: Optional[float] = Field(None, ge=0, le=120)

    @field_validator("bg_color1", "bg_color2")
    @classmethod
    def _color(cls, value: Optional[str]) -> Optional[str]:
        return _check_color(value)

    @field_validator("bg_image_url")
    @classmethod
    def _image(cls, value: Optional[str]) -> Optional[str]:
        return _check_image(value)


class ThemeRead(ThemeFields):
    id: str
    scope: str
    user_id: Optional[str] = None
    editable: bool = False
    created_at: str
    updated_at: str


class Selection(BaseModel):
    app: str
    theme: Optional[ThemeRead] = None


class SelectionIn(BaseModel):
    theme_id: Optional[str] = None


class ImageOut(BaseModel):
    url: str


# ─── helpers ─────────────────────────────────────────────────────────────────


def read(theme: Theme, *, user_id: str, admin: bool) -> ThemeRead:
    return ThemeRead(
        id=theme.id, scope=theme.scope, user_id=theme.user_id, name=theme.name, mode=theme.mode,
        bg_type=theme.bg_type, bg_color1=theme.bg_color1, bg_color2=theme.bg_color2,
        bg_image_url=theme.bg_image_url, bg_image_mode=theme.bg_image_mode, bg_blur=theme.bg_blur,
        glass_opacity=theme.glass_opacity, glass_blur=theme.glass_blur, shadow_size=theme.shadow_size,
        shadow_blur=theme.shadow_blur,
        editable=(admin if theme.scope == SCOPE_INSTANCE else theme.user_id == user_id),
        created_at=theme.created_at.isoformat(), updated_at=theme.updated_at.isoformat(),
    )


def visible(session: Session, theme_id: str, user_id: str) -> Optional[Theme]:
    theme = session.get(Theme, theme_id)
    if theme is None or (theme.scope == SCOPE_USER and theme.user_id != user_id):
        return None
    return theme


def check_app(app: str) -> None:
    if app not in THEMED_APPS:
        raise access.bad_request("bad_app", f"App must be one of: {', '.join(THEMED_APPS)}")


def image_dir() -> Path:
    path = settings.resolved_data_dir / "theme-images"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ─── the library ─────────────────────────────────────────────────────────────


@router.get("", response_model=ListResponse[ThemeRead])
def list_themes(user: CurrentUser, session: Session = Depends(get_session)):
    admin = access.caller_is_admin(session, user)
    rows = session.exec(
        select(Theme)
        .where(or_(Theme.scope == SCOPE_INSTANCE, (Theme.scope == SCOPE_USER) & (Theme.user_id == user.id)))
        .order_by(Theme.scope, Theme.name)
    ).all()
    data = [read(t, user_id=user.id, admin=admin) for t in rows]
    return ListResponse(data=data, total=len(data), limit=len(data), offset=0)


@router.post("", response_model=DataResponse[ThemeRead], status_code=status.HTTP_201_CREATED)
def create_theme(payload: ThemeCreate, user: CurrentUser, session: Session = Depends(get_session)):
    admin = access.caller_is_admin(session, user)
    access.require_scope(payload.scope, admin)
    theme = Theme(scope=payload.scope, user_id=access.owner_for(payload.scope, user),
                  **payload.model_dump(exclude={"scope"}))
    session.add(theme)
    session.commit()
    session.refresh(theme)
    return DataResponse(data=read(theme, user_id=user.id, admin=admin))


@router.patch("/{theme_id}", response_model=DataResponse[ThemeRead])
def update_theme(theme_id: str, payload: ThemeUpdate, user: CurrentUser, session: Session = Depends(get_session)):
    admin = access.caller_is_admin(session, user)
    theme = visible(session, theme_id, user.id)
    if theme is None:
        raise access.not_found("theme")
    if not access.can_write(theme.scope, theme.user_id, user, admin):
        raise access.admin_required("Only an administrator can change a shared theme")
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == "name" and value is None:
            continue
        setattr(theme, field, value)
    theme.updated_at = utcnow()
    session.add(theme)
    session.commit()
    session.refresh(theme)
    return DataResponse(data=read(theme, user_id=user.id, admin=admin))


@router.delete("/{theme_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_theme(theme_id: str, user: CurrentUser, session: Session = Depends(get_session)):
    admin = access.caller_is_admin(session, user)
    theme = visible(session, theme_id, user.id)
    if theme is None:
        raise access.not_found("theme")
    if not access.can_write(theme.scope, theme.user_id, user, admin):
        raise access.admin_required("Only an administrator can delete a shared theme")
    # Whoever was wearing it goes back to plain light or dark, rather than to a choice
    # that names nothing.
    wearing = json.dumps(theme.id)
    for pref in session.exec(select(UserPreference).where(
            UserPreference.key.like("theme.%"), UserPreference.value == wearing)).all():
        session.delete(pref)
    session.delete(theme)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ─── background images ───────────────────────────────────────────────────────


@router.post("/images", response_model=DataResponse[ImageOut], status_code=status.HTTP_201_CREATED)
async def upload_image(request: Request, user: CurrentUser):
    """The image itself as the request body, with its own Content-Type. Not a form: the
    one file is all there is, and it saves a multipart parser."""
    kind = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if kind not in IMAGE_TYPES:
        raise access.bad_request("bad_image", "Upload a PNG, JPEG, WebP or GIF image")
    body = await request.body()
    if not body:
        raise access.bad_request("bad_image", "The image is empty")
    if len(body) > MAX_IMAGE_BYTES:
        raise access.bad_request("image_too_large", "Background images can be at most 8 MB")
    name = secrets.token_urlsafe(24) + IMAGE_TYPES[kind]
    (image_dir() / name).write_bytes(body)
    return DataResponse(data=ImageOut(url=f"{settings.app_base_url.rstrip('/')}/api/theme-images/{name}"))


@public_router.get("/theme-images/{name}")
def get_image(name: str):
    if not _IMAGE_NAME.match(name) or not (image_dir() / name).is_file():
        raise access.not_found("image")
    # Never changes once written (a new upload gets a new name), so caches may keep it.
    return FileResponse(image_dir() / name, headers={"Cache-Control": "public, max-age=31536000, immutable"})


# ─── which theme each app wears ──────────────────────────────────────────────


@router.get("/selected", response_model=ListResponse[Selection])
def all_selections(user: CurrentUser, session: Session = Depends(get_session)):
    data = [_selection(session, user, app) for app in THEMED_APPS]
    return ListResponse(data=data, total=len(data), limit=len(data), offset=0)


@router.get("/selected/{app}", response_model=DataResponse[Selection])
def get_selection(app: str, user: CurrentUser, session: Session = Depends(get_session)):
    check_app(app)
    return DataResponse(data=_selection(session, user, app))


@router.put("/selected/{app}", response_model=DataResponse[Selection])
def put_selection(app: str, payload: SelectionIn, user: CurrentUser, session: Session = Depends(get_session)):
    check_app(app)
    pref = session.exec(select(UserPreference).where(
        UserPreference.user_id == user.id, UserPreference.key == preference_key(app))).first()
    if payload.theme_id is None:
        if pref is not None:
            session.delete(pref)
            session.commit()
        return DataResponse(data=Selection(app=app))
    if visible(session, payload.theme_id, user.id) is None:
        raise access.not_found("theme")
    if pref is None:
        pref = UserPreference(user_id=user.id, key=preference_key(app))
    pref.value, pref.updated_at = json.dumps(payload.theme_id), utcnow()
    session.add(pref)
    session.commit()
    return DataResponse(data=_selection(session, user, app))


def _selection(session: Session, user, app: str) -> Selection:
    pref = session.exec(select(UserPreference).where(
        UserPreference.user_id == user.id, UserPreference.key == preference_key(app))).first()
    theme_id = json.loads(pref.value) if pref else None
    theme = visible(session, theme_id, user.id) if isinstance(theme_id, str) else None
    if theme is None:
        return Selection(app=app)
    return Selection(app=app, theme=read(theme, user_id=user.id,
                                         admin=access.caller_is_admin(session, user)))
