"""fal.ai image and video jobs through the suite.

Thin by design (M3): the caller names a fal endpoint and sends that endpoint's own request
body, and gets back a job it can poll, resume by id after a restart, and cancel. What the
suite adds is the key, the usage ledger and the billed cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from gecko_suite_client.client import SuiteClient

TERMINAL = ("completed", "failed", "cancelled")


@dataclass
class MediaJob:
    id: str
    kind: str
    endpoint_id: str
    # queued | running | completed | failed | cancelled
    status: str
    queue_position: Optional[int] = None
    error: Optional[str] = None
    # fal's result body, once completed: dialect-specific, so the caller reads its own.
    result: Optional[dict] = None
    billable_units: Optional[str] = None
    # What the suite recorded. None is unknown, never free.
    cost: Optional[float] = None
    currency: Optional[str] = None
    cost_estimated: Optional[bool] = None
    usage_event_id: Optional[str] = None

    @property
    def done(self) -> bool:
        return self.status in TERMINAL

    @classmethod
    def from_data(cls, data: dict) -> "MediaJob":
        return cls(**{name: data.get(name) for name in cls.__dataclass_fields__})


class SuiteMedia:
    def __init__(self, client: SuiteClient, *, app: Optional[str] = None, subject_ref: Optional[str] = None):
        self.client = client
        self.app = app or client.app
        self.subject_ref = subject_ref

    def submit(self, kind: str, endpoint_id: str, input: dict[str, Any], *,
               subject_ref: Optional[str] = None) -> MediaJob:
        """Queue one generation. `kind` is "image" or "video". Returns at once."""
        body: dict[str, Any] = {"kind": kind, "endpoint_id": endpoint_id, "input": input}
        if self.app:
            body["app"] = self.app
        if subject_ref or self.subject_ref:
            body["subject_ref"] = subject_ref or self.subject_ref
        return MediaJob.from_data(self.client.request("POST", "/api/v1/ai/media/jobs", json=body))

    def get(self, job_id: str) -> MediaJob:
        """The job's state. Asking is also what completes it: the suite fetches the result
        and records the cost on the first call that finds fal finished."""
        return MediaJob.from_data(self.client.request("GET", f"/api/v1/ai/media/jobs/{job_id}"))

    def cancel(self, job_id: str) -> MediaJob:
        return MediaJob.from_data(self.client.request("DELETE", f"/api/v1/ai/media/jobs/{job_id}"))
