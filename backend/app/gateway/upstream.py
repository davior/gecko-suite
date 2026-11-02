"""One request to a vendor: retries, the timeout split, streaming, and error sentences.

Ported from GVC's `providers/_upstream.py` (from GAM, from gecko-notes'
`_post_upstream`), on `app.outbound` so tests fake every vendor. Two decisions carried
over:

- **Only 429 and 503 are retried.** They mean "ask again", and nothing was produced or
  billed on the attempt that returned them. A 400 or a 401 fails identically forever.
- **The timeout is split.** A blocking completion returns no bytes until the whole
  answer exists, so the read window covers generation; connect, write and pool stay
  short so a dead endpoint fails in seconds.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator, Mapping, Optional

import httpx

from app import outbound
from app.gateway.types import UpstreamError

logger = logging.getLogger(__name__)

RETRY_STATUS_CODES = frozenset({429, 503})
MAX_ATTEMPTS = 3
RETRY_BASE_DELAY = 0.5
# A vendor asking for a longer wait is telling us to come back later, not to hold a
# request open. Clamped: for a custom endpoint the header is attacker-adjacent input.
MAX_RETRY_AFTER = 30.0


def split_timeout(timeout: float) -> httpx.Timeout:
    return httpx.Timeout(
        timeout, connect=min(timeout, 10.0), write=min(timeout, 30.0), pool=min(timeout, 10.0)
    )


def _retry_after(response: httpx.Response, fallback: float) -> float:
    raw = response.headers.get("retry-after")
    try:
        seconds = float(raw.strip()) if raw else 0.0
    except ValueError:
        return fallback
    return min(seconds, MAX_RETRY_AFTER) if seconds > 0 else fallback


def post_json(
    url: str,
    *,
    headers: Mapping[str, str],
    body: Mapping[str, Any],
    timeout: float,
    label: str,
    sleep=time.sleep,
) -> httpx.Response:
    """POST, retrying only what is worth retrying. An HTTP error status is returned
    as-is, because only the caller knows how to read its body."""
    try:
        with outbound.client(timeout=split_timeout(timeout)) as http:
            for attempt in range(MAX_ATTEMPTS):
                response = http.post(url, headers=dict(headers), json=dict(body))
                if response.status_code not in RETRY_STATUS_CODES or attempt == MAX_ATTEMPTS - 1:
                    return response
                delay = _retry_after(response, RETRY_BASE_DELAY * (2**attempt))
                logger.info("%s returned %d; retrying in %.1fs", label, response.status_code, delay)
                sleep(delay)
            return response
    except httpx.TimeoutException as exc:
        raise UpstreamError(f"{label} did not respond in time", status=504,
                            code="upstream_timeout") from exc
    except httpx.RequestError as exc:
        # The exception type, not its text: its message can carry the URL, and a custom
        # provider's URL is user-supplied.
        raise UpstreamError(f"Could not reach {label} ({type(exc).__name__})",
                            code="upstream_unreachable") from exc


@contextmanager
def stream_lines(
    url: str, *, headers: Mapping[str, str], body: Mapping[str, Any], read_timeout: float, label: str
) -> Iterator[Iterator[str]]:
    """Open a streamed POST and yield its lines.

    Not retried: a stream that has started producing output has been billed for it. The
    read timeout bounds the gap *between* events (always small, vendors send pings), not
    the whole response, which is why streaming survives generations that would time out
    a blocking request.
    """
    try:
        with outbound.client(timeout=split_timeout(read_timeout)) as http:
            with http.stream("POST", url, headers=dict(headers), json=dict(body)) as response:
                if response.status_code >= 400:
                    response.read()
                    raise http_error(response, label)
                yield response.iter_lines()
    except httpx.TimeoutException as exc:
        raise UpstreamError(f"{label} stopped sending data", status=504,
                            code="upstream_timeout") from exc
    except httpx.RequestError as exc:
        raise UpstreamError(f"Could not reach {label} ({type(exc).__name__})",
                            code="upstream_unreachable") from exc


def http_error(response: httpx.Response, label: str) -> UpstreamError:
    """An UpstreamError for a vendor's error status, in words the caller can show.

    401/403 get their own code because their remedy is a settings change, not a retry,
    and the calling app should say which.
    """
    detail = error_detail(response)
    if response.status_code in (401, 403):
        return UpstreamError(f"{label} rejected its API key: {detail}", code="upstream_auth")
    if response.status_code == 429:
        return UpstreamError(f"{label} is rate limiting or out of quota: {detail}",
                             status=429, code="upstream_rate_limited")
    return UpstreamError(f"{label}: {detail}")


def error_detail(response: httpx.Response) -> str:
    """A sentence from an error response, whatever shape it arrived in.

    Every vendor nests its message somewhere different and some send HTML, so this tries
    the known shapes and falls back to the status code rather than pasting markup.
    """
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        if isinstance(error, str):
            return error
        for key in ("message", "detail", "err_msg"):
            if isinstance(body.get(key), str):
                return body[key]
    return f"HTTP {response.status_code}"


def as_int(mapping: Optional[Mapping[str, Any]], key: str) -> int:
    try:
        return int((mapping or {}).get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0
