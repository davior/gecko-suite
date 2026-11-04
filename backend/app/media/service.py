"""Running a fal.ai request for an app: submit, poll, cancel, and bill once.

Thin on purpose (M3). The caller says which fal endpoint and sends that endpoint's own
request body; the suite adds the key, keeps the queue ids, and records what it cost. What
each endpoint's body looks like (`image_url` or `image_urls[]`, `"5"` or `5` seconds) is
the caller's catalogue, which GAM keeps until the suite takes the model catalogue over.

The key comes from the caller's visible fal provider, not from task routing: routing is
for choosing between models, and fal's models are chosen by endpoint id.
"""

from __future__ import annotations

import json
from typing import Optional

from fastapi import HTTPException, status
from sqlmodel import Session, col, select

from app import crypto
from app.auth import UserCtx
from app.clock import utcnow
from app.gateway.types import UpstreamError
from app.media import fal, pricing
from app.models.ai import AIProvider
from app.models.media import MediaJob
from app.models.usage import UsageEvent
from app.services.access import app_for, bad_request, not_found
from app.services.registry import visible_providers_query
from app.vocab import SCOPE_USER, VENDOR_FAL

KINDS = ("image", "video")
TERMINAL = ("completed", "failed", "cancelled")


def fal_provider(session: Session, user_id: str) -> AIProvider:
    """The fal.ai provider this person's request goes through: their own, else the shared
    one. A provider with no usable key is skipped, not an error: someone with a broken
    personal key should still reach the shared one."""
    rows = session.exec(
        visible_providers_query(user_id).where(AIProvider.vendor == VENDOR_FAL,
                                               col(AIProvider.enabled).is_(True))
        .order_by(col(AIProvider.created_at))).all()
    rows = [r for r in rows if crypto.decrypt(r.api_key)]
    # Personal first: it is what the person chose for themselves.
    rows.sort(key=lambda r: 0 if r.scope == SCOPE_USER else 1)
    if not rows:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={
            "code": "ai_not_configured",
            "message": "No fal.ai key is set up. Add a fal.ai provider in Gecko Suite."})
    return rows[0]


def _key(session: Session, job: MediaJob) -> str:
    """The key for a job already started: the provider it began on, whatever has changed
    since, so a poll never moves to another account's key. If that provider is gone or
    switched off the job cannot be followed, and says so."""
    provider = session.get(AIProvider, job.provider_id) if job.provider_id else None
    key = crypto.decrypt(provider.api_key) if provider is not None and provider.enabled else ""
    if not key:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={
            "code": "provider_key_missing",
            "message": f"{job.provider_name or 'The fal.ai provider'} is no longer available, "
                       "so this request can't be followed."})
    return key


def owned(session: Session, user: UserCtx, job_id: str) -> MediaJob:
    job = session.get(MediaJob, job_id)
    # Someone else's is reported as missing, as everywhere in this API.
    if job is None or job.user_id != user.id:
        raise not_found("media job")
    return job


def submit(session: Session, user: UserCtx, *, kind: str, endpoint_id: str, body: dict,
           app: Optional[str], subject_ref: Optional[str]) -> MediaJob:
    if kind not in KINDS:
        raise bad_request("bad_kind", "kind must be image or video")
    if not fal.valid_endpoint_id(endpoint_id):
        raise bad_request("bad_endpoint", f"{endpoint_id!r} is not a fal.ai endpoint id")
    provider = fal_provider(session, user.id)
    try:
        queued = fal.submit(endpoint_id, body, api_key=crypto.decrypt(provider.api_key))
    except UpstreamError as exc:
        raise HTTPException(status_code=exc.status,
                            detail={"code": exc.code, "message": exc.message}) from exc
    job = MediaJob(user_id=user.id, app=app_for(user, app), kind=kind, endpoint_id=endpoint_id,
                   provider_id=provider.id, provider_name=provider.name, subject_ref=subject_ref,
                   request=json.dumps(queued.to_dict()))
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def poll(session: Session, job: MediaJob) -> MediaJob:
    """Bring a job up to date with fal, and on completion fetch the result and bill it.

    A transient fal outage (429/5xx on a status check) leaves the job as it was: a video
    four minutes in must not be abandoned, and billed for nothing, over one bad gateway.
    """
    if job.status in TERMINAL:
        return job
    queued = fal.QueuedRequest.from_dict(json.loads(job.request or "{}"))
    if queued is None:
        return _finish(session, job, "failed", error="The request's queue details were lost.")
    key = _key(session, job)
    try:
        current = fal.status(queued, api_key=key)
    except UpstreamError as exc:
        if exc.code == "upstream_unavailable":
            return job
        raise HTTPException(status_code=exc.status,
                            detail={"code": exc.code, "message": exc.message}) from exc

    if current.status != fal.STATUS_COMPLETED:
        job.status = "running" if current.status == fal.STATUS_IN_PROGRESS else "queued"
        job.queue_position = current.queue_position
        job.updated_at = utcnow()
        session.add(job)
        session.commit()
        return job
    if current.error:
        return _finish(session, job, "failed", error=current.error)
    return _complete(session, job, queued, key)


def _complete(session: Session, job: MediaJob, queued: fal.QueuedRequest, key: str) -> MediaJob:
    try:
        fetched = fal.result(queued, api_key=key)
    except UpstreamError as exc:
        if exc.code == "upstream_unavailable":
            return job  # try again on the next poll
        return _finish(session, job, "failed", error=exc.message)

    # Two pollers can arrive together. Whoever commits first bills; the other finds it done.
    session.refresh(job)
    if job.status in TERMINAL:
        return job
    price = pricing.list_price(job.provider_id or "", job.endpoint_id, api_key=key)
    cost = pricing.compute(is_video=job.kind == "video", billable_units=fetched.billable_units,
                           price=price, outputs=pricing.facts_from_body(fetched.body))
    event = UsageEvent(
        user_id=job.user_id, app=job.app, task="video.generate" if job.kind == "video" else "image.generate",
        kind=job.kind, provider_id=job.provider_id, provider_name=job.provider_name, vendor=VENDOR_FAL,
        model=job.endpoint_id, units=cost.units, unit_type=cost.unit_type, cost=cost.cost,
        currency=cost.currency, cost_estimated=cost.estimated is not False,
        external_ref=fetched.request_id or queued.request_id, subject_ref=job.subject_ref)
    session.add(event)
    session.flush()
    job.status = "completed"
    job.result = json.dumps(fetched.body)
    job.billable_units = fetched.billable_units
    job.usage_event_id = event.id
    job.updated_at = utcnow()
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def _finish(session: Session, job: MediaJob, state: str, *, error: Optional[str] = None) -> MediaJob:
    job.status = state
    job.error = error
    job.updated_at = utcnow()
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def cancel(session: Session, job: MediaJob) -> MediaJob:
    """Stop a job. Asks fal to stop, best effort, then marks it cancelled here.

    Nothing is billed here for a cancelled job: the cost is only known when the result is
    fetched, and a cancelled job's never is. fal may still bill a request that was already
    running; that is fal's account of it, not this ledger's.
    """
    if job.status in TERMINAL:
        return job
    queued = fal.QueuedRequest.from_dict(json.loads(job.request or "{}"))
    if queued is not None:
        try:
            fal.cancel(queued, api_key=_key(session, job))
        except HTTPException:
            pass  # a provider that is gone cannot be asked; the job is still cancelled here
    return _finish(session, job, "cancelled")


def usage_of(session: Session, job: MediaJob) -> Optional[UsageEvent]:
    return session.get(UsageEvent, job.usage_event_id) if job.usage_event_id else None
