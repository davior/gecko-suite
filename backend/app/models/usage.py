"""One row per billable call the suite made, whichever app asked for it.

The union of the three siblings' `UsageEvent`s (Notes: tts/stt/ai/image; GAM: adds
`asset_id` and fal's billed cost; GVC: ai only), with the app that asked and the task
recorded, and the content it was about as an opaque `subject_ref` ("gam:asset:<id>").

Deliberately no foreign keys. Deleting a provider, a model or a user must not erase what
was spent through them, so the names are copied onto the row at the time of the call.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.clock import utcnow


class UsageEvent(SQLModel, table=True):
    __tablename__ = "usage_event"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(index=True)
    app: Optional[str] = Field(default=None, index=True)
    task: Optional[str] = None
    # ai | embed | image | video | tts | stt | search
    kind: str
    provider_id: Optional[str] = None
    provider_name: str = ""
    vendor: str = ""
    # The model that actually answered (it can differ from the one routed to, when a
    # vendor falls back after a refusal).
    model: str = ""

    # Generic units, for kinds that are not token-priced: seconds, images, characters.
    units: float = 0
    unit_type: str = ""
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cache_read_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None

    # None when the price is unknown, never 0: a stored 0.0 claims the call was free.
    cost: Optional[float] = None
    currency: Optional[str] = None
    # True for a list-price estimate; False only for a cost the vendor billed exactly
    # (fal's x-fal-billable-units, from M3).
    cost_estimated: bool = True
    # ok | error. An error row is a reply that was billed and then could not be used.
    status: str = "ok"
    external_ref: Optional[str] = None
    subject_ref: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
