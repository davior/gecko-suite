"""fal.ai image and video generation, as jobs. See `app/media/service.py`."""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.auth import CurrentUser
from app.database import get_session
from app.media import service
from app.models.media import MediaJob
from app.schemas import DataResponse

router = APIRouter()


class MediaJobCreate(BaseModel):
    kind: str
    # fal's id for the endpoint: `fal-ai/flux-pro/v1.1`.
    endpoint_id: str = Field(max_length=200)
    # That endpoint's own request body, passed through as given.
    input: dict[str, Any]
    app: Optional[str] = None
    subject_ref: Optional[str] = Field(default=None, max_length=300)


class MediaJobRead(BaseModel):
    id: str
    kind: str
    endpoint_id: str
    # queued | running | completed | failed | cancelled
    status: str
    queue_position: Optional[int] = None
    error: Optional[str] = None
    # fal's result body, once completed.
    result: Optional[dict] = None
    billable_units: Optional[str] = None
    cost: Optional[float] = None
    currency: Optional[str] = None
    cost_estimated: Optional[bool] = None
    usage_event_id: Optional[str] = None
    provider: str = ""


def _read(session: Session, job: MediaJob) -> MediaJobRead:
    event = service.usage_of(session, job)
    return MediaJobRead(
        id=job.id, kind=job.kind, endpoint_id=job.endpoint_id, status=job.status,
        queue_position=job.queue_position, error=job.error,
        result=json.loads(job.result) if job.result else None, billable_units=job.billable_units,
        cost=event.cost if event else None, currency=event.currency if event else None,
        cost_estimated=event.cost_estimated if event and event.cost is not None else None,
        usage_event_id=job.usage_event_id, provider=job.provider_name)


@router.post("/jobs", response_model=DataResponse[MediaJobRead], status_code=202)
def create_job(body: MediaJobCreate, user: CurrentUser, session: Session = Depends(get_session)):
    """Queue one generation. Returns at once; poll the job."""
    job = service.submit(session, user, kind=body.kind, endpoint_id=body.endpoint_id,
                         body=body.input, app=body.app, subject_ref=body.subject_ref)
    return DataResponse(data=_read(session, job))


@router.get("/jobs/{job_id}", response_model=DataResponse[MediaJobRead])
def get_job(job_id: str, user: CurrentUser, session: Session = Depends(get_session)):
    """The job's state, brought up to date with fal. On completion this is also where the
    result is fetched and the cost recorded, once however often it is asked."""
    job = service.poll(session, service.owned(session, user, job_id))
    return DataResponse(data=_read(session, job))


@router.delete("/jobs/{job_id}", response_model=DataResponse[MediaJobRead])
def cancel_job(job_id: str, user: CurrentUser, session: Session = Depends(get_session)):
    job = service.cancel(session, service.owned(session, user, job_id))
    return DataResponse(data=_read(session, job))
