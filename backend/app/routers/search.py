"""Web search: the backends people set up, and the search the apps' assistants call."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlmodel import Session

from app import crypto
from app.auth import CurrentUser, UserCtx
from app.clock import utcnow
from app.database import get_session
from app.models.library import WebSearchProvider
from app.models.usage import UsageEvent
from app.routers.library import visible_query, visible_row
from app.safe_url import require_safe_external_url
from app.schemas import DataResponse, ListResponse
from app.search.web import KINDS, MAX_IMAGES, SearchError, search, search_images
from app.services import access

router = APIRouter()

# The backends nobody pays per query for. Brave and Tavily bill by plan, so their cost
# per call is unknown here and recorded as None, not 0.
FREE_KINDS = frozenset({"duckduckgo", "searxng"})


class SearchProviderRead(BaseModel):
    id: str
    scope: str
    name: str
    kind: str
    base_url: Optional[str] = None
    enabled: bool
    api_key_configured: bool
    writable: bool


class SearchProviderCreate(BaseModel):
    scope: str = "user"
    name: str = Field(min_length=1, max_length=200)
    kind: str
    api_key: str = ""
    base_url: Optional[str] = None
    enabled: bool = True


class SearchProviderUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    api_key: Optional[str] = None  # three-state, as for AI providers
    base_url: Optional[str] = None
    enabled: Optional[bool] = None


def _read(row: WebSearchProvider, writable: bool) -> SearchProviderRead:
    return SearchProviderRead(id=row.id, scope=row.scope, name=row.name, kind=row.kind, base_url=row.base_url,
                              enabled=row.enabled, api_key_configured=bool(row.api_key), writable=writable)


def _check_base_url(base_url: Optional[str]) -> None:
    # Notes checks the SearXNG URL the same way before storing it: the suite fetches it.
    if base_url:
        require_safe_external_url(base_url)


@router.get("/providers", response_model=ListResponse[SearchProviderRead])
def list_search_providers(user: CurrentUser, session: Session = Depends(get_session)):
    rows = session.exec(visible_query_providers(user.id)).all()
    admin = access.caller_is_admin(session, user)
    data = [_read(r, access.can_write(r.scope, r.user_id, user, admin)) for r in rows]
    return ListResponse(data=data, total=len(data), limit=len(data), offset=0)


def visible_query_providers(user_id: str):
    # WebSearchProvider has no `app` column, so the shared visibility query is used
    # without one.
    return visible_query(WebSearchProvider, user_id, None).order_by(
        WebSearchProvider.scope.desc(), WebSearchProvider.created_at  # user rows first
    )


@router.post("/providers", response_model=DataResponse[SearchProviderRead], status_code=201)
def create_search_provider(payload: SearchProviderCreate, user: CurrentUser, session: Session = Depends(get_session)):
    access.require_scope(payload.scope, access.caller_is_admin(session, user))
    if payload.kind not in KINDS:
        raise access.bad_request("bad_kind", f"Unknown search backend: {payload.kind}")
    _check_base_url(payload.base_url)
    row = WebSearchProvider(scope=payload.scope, user_id=access.owner_for(payload.scope, user), name=payload.name,
                            kind=payload.kind, api_key=crypto.encrypt(payload.api_key),
                            base_url=payload.base_url or None, enabled=payload.enabled)
    session.add(row)
    session.commit()
    session.refresh(row)
    return DataResponse(data=_read(row, True))


def _writable(session: Session, user: UserCtx, row_id: str) -> WebSearchProvider:
    row = visible_row(session, WebSearchProvider, row_id, user.id)
    if row is None:
        raise access.not_found("search provider")
    if not access.can_write(row.scope, row.user_id, user, access.caller_is_admin(session, user)):
        raise access.admin_required()
    return row


@router.patch("/providers/{row_id}", response_model=DataResponse[SearchProviderRead])
def update_search_provider(row_id: str, payload: SearchProviderUpdate, user: CurrentUser,
                           session: Session = Depends(get_session)):
    row = _writable(session, user, row_id)
    if payload.base_url is not None:
        _check_base_url(payload.base_url)
        row.base_url = payload.base_url or None
    if payload.name is not None:
        row.name = payload.name
    if payload.enabled is not None:
        row.enabled = payload.enabled
    if payload.api_key is not None:
        row.api_key = crypto.encrypt(payload.api_key)
    row.updated_at = utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return DataResponse(data=_read(row, True))


@router.delete("/providers/{row_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None,
               response_class=Response)
def delete_search_provider(row_id: str, user: CurrentUser, session: Session = Depends(get_session)) -> None:
    session.delete(_writable(session, user, row_id))
    session.commit()


class WebSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    count: Optional[int] = Field(default=None, ge=1, le=10)
    app: Optional[str] = None
    subject_ref: Optional[str] = Field(default=None, max_length=300)


class WebSearchHit(BaseModel):
    title: str
    url: str
    snippet: str = ""
    published: Optional[str] = None


class WebSearchResult(BaseModel):
    provider: str
    kind: str
    results: list[WebSearchHit]


def _backend(session: Session, user: UserCtx) -> Optional[WebSearchProvider]:
    """The caller's own enabled backend, else the shared one; None means DuckDuckGo."""
    return next((r for r in session.exec(visible_query_providers(user.id)).all() if r.enabled), None)


def _record(session: Session, user: UserCtx, app: str, task: str, row: Optional[WebSearchProvider],
            kind: str, name: str, subject_ref: Optional[str]) -> None:
    session.add(UsageEvent(
        user_id=user.id, app=app, task=task, kind="search", provider_id=row.id if row else None,
        provider_name=name, vendor=kind, units=1, unit_type="queries",
        cost=0.0 if kind in FREE_KINDS else None, currency="USD" if kind in FREE_KINDS else None,
        subject_ref=subject_ref,
    ))
    session.commit()


@router.post("/web", response_model=DataResponse[WebSearchResult])
def web_search(payload: WebSearchRequest, user: CurrentUser, session: Session = Depends(get_session)):
    """Search with the caller's own backend, else the shared one, else DuckDuckGo.

    DuckDuckGo last because it needs no key: a fresh install has working search before
    anybody signs up for anything, which is Notes' default and the reason for it.
    """
    app = access.app_for(user, payload.app)
    row = _backend(session, user)
    kind = row.kind if row else "duckduckgo"
    name = row.name if row else "DuckDuckGo"
    try:
        hits = search(kind=kind, query=payload.query, api_key=crypto.decrypt(row.api_key) if row else "",
                      base_url=(row.base_url or "") if row else "", count=payload.count)
    except SearchError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})

    _record(session, user, app, "search.web", row, kind, name, payload.subject_ref)
    return DataResponse(data=WebSearchResult(provider=name, kind=kind,
                                             results=[WebSearchHit(**vars(h)) for h in hits]))


class ImageSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    count: Optional[int] = Field(default=None, ge=1, le=MAX_IMAGES)
    app: Optional[str] = None
    subject_ref: Optional[str] = Field(default=None, max_length=300)


class ImageSearchHit(BaseModel):
    image_url: str
    page_url: Optional[str] = None
    title: str = ""
    thumbnail_url: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    description: str = ""


class ImageSearchResult(BaseModel):
    provider: str
    kind: str
    results: list[ImageSearchHit]


@router.post("/images", response_model=DataResponse[ImageSearchResult])
def image_search(payload: ImageSearchRequest, user: CurrentUser, session: Session = Depends(get_session)):
    """Pictures from the same backend `/web` would use. Candidates only: the suite never
    downloads one, and says nothing about who may use it, because no backend knows."""
    app = access.app_for(user, payload.app)
    row = _backend(session, user)
    kind = row.kind if row else "duckduckgo"
    name = row.name if row else "DuckDuckGo"
    try:
        hits = search_images(kind=kind, query=payload.query, api_key=crypto.decrypt(row.api_key) if row else "",
                             base_url=(row.base_url or "") if row else "", count=payload.count)
    except SearchError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})

    _record(session, user, app, "search.images", row, kind, name, payload.subject_ref)
    return DataResponse(data=ImageSearchResult(provider=name, kind=kind,
                                               results=[ImageSearchHit(**vars(h)) for h in hits]))

