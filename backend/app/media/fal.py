"""fal.ai's queue API, spoken over raw httpx.

Ported from GAM's `generation/fal.py`, whose reasoning holds: raw rather than through
fal's own clients because neither exposes response headers and `x-fal-billable-units` on
the result is the whole of exact costing; the queue for every kind, never the synchronous
`fal.run`, because a video takes minutes and a dropped connection there loses a result fal
still bills for.

Changed from GAM's: failures are `UpstreamError`s (this service's one error type), and the
URLs fal returns are checked against the configured queue host *before they are stored*,
since this service sends the API key to whatever is stored on every poll.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

import httpx

from app.config import settings
from app.gateway import upstream
from app.gateway.types import UpstreamError

logger = logging.getLogger(__name__)

LABEL = "fal.ai"
SUBMIT_TIMEOUT = 120.0
CALL_TIMEOUT = 30.0
CANCEL_TIMEOUT = 10.0

STATUS_IN_QUEUE = "IN_QUEUE"
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_COMPLETED = "COMPLETED"

# Namespaces whose app id is three segments rather than two (fal's own clients encode the
# same exception).
_THREE_SEGMENT_NAMESPACES = frozenset({"workflows", "comfy"})

# `owner/alias[/path...]`. Checked because the id is spliced into a URL the key is sent to:
# a `..` or a `?` in it must not be able to change where that goes.
_ENDPOINT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*(/[A-Za-z0-9][A-Za-z0-9_.-]*){1,5}$")


def valid_endpoint_id(endpoint_id: str) -> bool:
    return bool(_ENDPOINT_ID.match(endpoint_id)) and ".." not in endpoint_id


@dataclass(frozen=True)
class QueuedRequest:
    request_id: str
    status_url: str
    response_url: str
    cancel_url: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> Optional["QueuedRequest"]:
        if not isinstance(data, dict):
            return None
        try:
            return cls(**{name: str(data[name]) for name in cls.__dataclass_fields__})
        except KeyError:
            return None


@dataclass(frozen=True)
class QueueStatus:
    status: str
    queue_position: Optional[int] = None
    # Set alongside COMPLETED when the run failed: fal reports a failure as completed.
    error: Optional[str] = None


@dataclass(frozen=True)
class QueueResult:
    body: dict
    billable_units: Optional[str]
    request_id: Optional[str]


def auth_headers(api_key: str) -> dict[str, str]:
    """fal's scheme is `Key`, not `Bearer`, sent literally."""
    return {"Authorization": f"Key {api_key}"}


def submit(endpoint_id: str, body: Mapping[str, Any], *, api_key: str) -> QueuedRequest:
    """Queue one request. Retries 429/503 only: nothing is billed on those."""
    response = upstream.post_json(
        f"{_queue_base()}/{endpoint_id}", headers=auth_headers(api_key), body=body,
        timeout=SUBMIT_TIMEOUT, label=LABEL)
    if response.status_code >= 400:
        raise error_for(response)
    data = _json(response)
    request_id = data.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise UpstreamError("fal.ai accepted the request but did not say what it was called")
    built = f"{_queue_base()}/{_app_id(endpoint_id)}/requests/{request_id}"
    return QueuedRequest(
        request_id=request_id,
        status_url=_same_origin_or(data.get("status_url"), f"{built}/status"),
        response_url=_same_origin_or(data.get("response_url"), built),
        cancel_url=_same_origin_or(data.get("cancel_url"), f"{built}/cancel"),
    )


def status(request: QueuedRequest, *, api_key: str) -> QueueStatus:
    response = upstream.request("GET", request.status_url, headers=auth_headers(api_key),
                                params={"logs": "0"}, timeout=CALL_TIMEOUT, label=LABEL)
    if response.status_code >= 400:
        raise error_for(response)
    data = _json(response)
    position = data.get("queue_position")
    if isinstance(position, bool) or not isinstance(position, int):
        position = None
    error = data.get("error")
    return QueueStatus(status=str(data.get("status") or ""), queue_position=position,
                       error=str(error) if error else None)


def result(request: QueuedRequest, *, api_key: str) -> QueueResult:
    response = upstream.request("GET", request.response_url, headers=auth_headers(api_key),
                                timeout=CALL_TIMEOUT, label=LABEL)
    if response.status_code >= 400:
        if response.status_code >= 500 and "x-fal-request-id" in response.headers:
            # The model's own failure, not the gateway's: fal stamps its request id on what
            # its servers answer. Not retryable, so not reported as "unavailable" (which
            # would make the caller poll it again until its deadline).
            raise UpstreamError(f"fal.ai could not generate this: {upstream.error_detail(response)}",
                                status=422, code="generation_failed")
        raise error_for(response)
    return QueueResult(body=_json(response),
                       billable_units=response.headers.get("x-fal-billable-units"),
                       request_id=response.headers.get("x-fal-request-id"))


def cancel(request: QueuedRequest, *, api_key: str) -> bool:
    """Ask fal to stop. Best effort, never raises; True if fal accepted it.

    202 means accepted, not stopped: a running job may finish anyway, and nothing here
    waits to find out. Sent because a request abandoned here keeps running, and billing,
    there.
    """
    try:
        response = upstream.request("PUT", request.cancel_url, headers=auth_headers(api_key),
                                    timeout=CANCEL_TIMEOUT, label=LABEL)
    except UpstreamError:
        logger.warning("Could not ask fal.ai to cancel request %s", request.request_id)
        return False
    return response.status_code < 400


def error_for(response: httpx.Response) -> UpstreamError:
    """A fal error response as a sentence somebody can act on."""
    code = response.status_code
    detail = upstream.error_detail(response)
    if code in (401, 403):
        # An exhausted balance arrives as a 403 too, as far as integrators report, and
        # "check the key" would send somebody to the wrong page.
        if "balance" in detail.lower():
            return UpstreamError(f"fal.ai refused the request: {detail}", status=402,
                                 code="upstream_quota")
        return UpstreamError("fal.ai rejected its API key. Check it in Gecko Suite's providers.",
                             code="upstream_auth")
    if code == 422:
        return UpstreamError(f"fal.ai refused the request: {_validation_detail(response, detail)}",
                             status=422, code="upstream_invalid")
    if code == 429 or code >= 500:
        return UpstreamError(f"fal.ai is not available right now ({detail})", status=503,
                             code="upstream_unavailable")
    return UpstreamError(f"fal.ai returned an error: {detail}")


def _validation_detail(response: httpx.Response, fallback: str) -> str:
    """fal's 422 body, `{"detail": [{"loc", "msg"}]}`, as `field: message`."""
    try:
        body = response.json()
    except ValueError:
        return fallback
    items = body.get("detail") if isinstance(body, dict) else None
    if not isinstance(items, list):
        return fallback
    messages = []
    for item in items:
        if not isinstance(item, dict):
            continue
        location = [str(part) for part in item.get("loc") or [] if part != "body"]
        message = str(item.get("msg") or "invalid")
        messages.append(f"{'.'.join(location)}: {message}" if location else message)
    return "; ".join(messages) or fallback


def _queue_base() -> str:
    return settings.fal_queue_base_url.rstrip("/")


def _app_id(endpoint_id: str) -> str:
    """The part of an endpoint id the queue's request URLs use: only `owner/alias`."""
    parts = endpoint_id.split("/")
    keep = 3 if parts[0] in _THREE_SEGMENT_NAMESPACES else 2
    return "/".join(parts[:keep])


def _same_origin_or(url: Any, fallback: str) -> str:
    if not isinstance(url, str) or not url:
        return fallback
    given, expected = urlsplit(url), urlsplit(_queue_base())
    if (given.scheme, given.netloc) != (expected.scheme, expected.netloc):
        logger.warning("fal.ai returned a queue URL on another host; using the documented one")
        return fallback
    return url


def _json(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError as exc:
        raise UpstreamError("fal.ai sent back something that was not JSON") from exc
    if not isinstance(data, dict):
        raise UpstreamError("fal.ai sent back something this service cannot read")
    return data
