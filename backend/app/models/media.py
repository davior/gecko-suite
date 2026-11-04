"""A fal.ai request the suite is running for an app.

The suite keeps the request's identity, not its content: the endpoint, who asked, and the
queue ids fal gave back. That is what lets an app resume after a restart by job id alone,
and what keeps fal's own URLs, which the API key is sent to, out of the callers' hands (an
app that could hand back a URL to poll could aim the key anywhere).

The result body is kept once fetched, because fal bills at that fetch and a second fetch
must not look like a second generation, and because a caller that crashed between getting
the result and saving the files should find it again.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.clock import utcnow


class MediaJob(SQLModel, table=True):
    __tablename__ = "media_job"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(index=True)
    app: Optional[str] = Field(default=None, index=True)
    # image | video
    kind: str
    endpoint_id: str
    # No foreign key: deleting a provider must not erase what was spent through it.
    provider_id: Optional[str] = None
    provider_name: str = ""
    subject_ref: Optional[str] = Field(default=None, index=True)

    # JSON of fal's QueuedRequest: request id and the three queue URLs.
    request: str = "{}"
    # queued | running | completed | failed | cancelled
    status: str = Field(default="queued", index=True)
    queue_position: Optional[int] = None
    error: Optional[str] = None
    # JSON: fal's result body, once fetched.
    result: Optional[str] = None
    billable_units: Optional[str] = None
    usage_event_id: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
