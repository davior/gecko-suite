"""What a fal.ai request cost.

Ported from GAM's `generation/pricing.py`, which holds the reasoning. fal reports the
billed *quantity* (`x-fal-billable-units`, in the endpoint's own unit) on the queue
result; the *price* per unit comes from its pricing API, fetched with the provider's own
key so a negotiated price is the one applied.

| units from          | price from  | cost            | cost_estimated |
|---------------------|-------------|-----------------|----------------|
| header              | pricing API | units × price   | False          |
| outputs (no header) | pricing API | derived × price | True           |
| anything            | no price    | None            | n/a            |

An unknown cost is None, never 0: a stored 0.0 claims the call was free.

Changed from GAM's: no catalogue list price to fall back on (the suite does not keep
fal's model catalogue yet; GAM does), and the output facts come from the result body (fal
sends each image's width and height) since this service does not download the files.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Iterable, Optional

from app.config import settings
from app.gateway import upstream
from app.gateway.types import UpstreamError
from app.media.fal import LABEL, auth_headers

logger = logging.getLogger(__name__)

PRICE_TTL_SECONDS = 6 * 3600
PRICING_TIMEOUT = 10.0

# fal rounds each image *up* to whole megapixels, and its megapixel is 1024², not 10⁶.
PIXELS_PER_MEGAPIXEL = 1024 * 1024

UNIT_TYPES = {
    "image": "images",
    "megapixel": "megapixels",
    "second": "generated_seconds",
    "video": "videos",
}


@dataclass(frozen=True)
class UnitPrice:
    unit_price: float
    unit: str
    currency: str


@dataclass(frozen=True)
class OutputFacts:
    width: Optional[int] = None
    height: Optional[int] = None
    duration_seconds: Optional[float] = None


@dataclass(frozen=True)
class Cost:
    units: int
    unit_type: str
    cost: Optional[float]
    currency: Optional[str]
    estimated: Optional[bool]


# Per (provider, endpoint): the price is fetched with that provider's key, and "custom
# pricing or discounts may be applied", so one key's price is not another's. In-process
# only: six hours of one small number, and a restart refetching it costs one request.
_cache: dict[tuple[str, str], tuple[float, UnitPrice]] = {}
_lock = threading.Lock()


def list_price(provider_id: str, endpoint_id: str, *, api_key: str) -> Optional[UnitPrice]:
    key = (provider_id, endpoint_id)
    now = time.monotonic()
    with _lock:
        cached = _cache.get(key)
        if cached and now - cached[0] < PRICE_TTL_SECONDS:
            return cached[1]
    price = _fetch(endpoint_id, api_key)
    # Only a found price is cached, so a blip does not pin six hours of unknown costs.
    if price is not None:
        with _lock:
            _cache[key] = (now, price)
    return price


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _fetch(endpoint_id: str, api_key: str) -> Optional[UnitPrice]:
    try:
        response = upstream.request(
            "GET", f"{settings.fal_api_base_url.rstrip('/')}/v1/models/pricing",
            headers=auth_headers(api_key), params={"endpoint_id": endpoint_id},
            timeout=PRICING_TIMEOUT, label=f"{LABEL} pricing")
    except UpstreamError as exc:
        logger.info("No price from fal.ai for %s: %s", endpoint_id, exc.message)
        return None
    if response.status_code >= 400:
        logger.info("No price from fal.ai for %s: HTTP %d", endpoint_id, response.status_code)
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    for entry in (body.get("prices") if isinstance(body, dict) else None) or []:
        if not isinstance(entry, dict) or entry.get("endpoint_id") != endpoint_id:
            continue
        unit_price = entry.get("unit_price")
        if isinstance(unit_price, bool) or not isinstance(unit_price, (int, float)):
            continue
        return UnitPrice(float(unit_price), str(entry.get("unit") or ""),
                         str(entry.get("currency") or "USD"))
    return None


def compute(*, is_video: bool, billable_units: Optional[str], price: Optional[UnitPrice],
            outputs: Iterable[OutputFacts]) -> Cost:
    outputs = list(outputs)
    unit = _unit_kind(price.unit if price else None)
    unit_type = UNIT_TYPES.get(unit or "", "videos" if is_video else "images")

    quantity = _parse_quantity(billable_units)
    from_header = quantity is not None
    if quantity is None and unit is not None:
        quantity = _derive(unit, outputs)

    if price is None or quantity is None:
        # Not priceable. The row is still written: a total that silently left out a
        # generation would read as complete.
        units = quantity if quantity is not None else len(outputs)
        return Cost(math.ceil(units), unit_type, None, None, None)
    return Cost(units=math.ceil(quantity), unit_type=unit_type,
                cost=round(quantity * price.unit_price, 6), currency=price.currency,
                estimated=not from_header)


def _parse_quantity(raw: Optional[str]) -> Optional[float]:
    if raw is None:
        return None
    try:
        value = float(raw.strip())
    except (ValueError, AttributeError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _unit_kind(unit: Optional[str]) -> Optional[str]:
    """fal's free-text unit as one of the four this service can derive and label.
    "compute seconds" and GPU units are not generated seconds and are left unmapped."""
    text = (unit or "").strip().lower()
    if "megapixel" in text:
        return "megapixel"
    if "compute" in text or "gpu" in text:
        return None
    if "second" in text:
        return "second"
    if "video" in text:
        return "video"
    if "image" in text:
        return "image"
    return None


def _derive(unit: str, outputs: list[OutputFacts]) -> Optional[float]:
    if not outputs:
        return None
    if unit in ("image", "video"):
        return float(len(outputs))
    if unit == "megapixel":
        if any(not o.width or not o.height for o in outputs):
            return None
        return float(sum(max(1, math.ceil(o.width * o.height / PIXELS_PER_MEGAPIXEL))
                         for o in outputs))
    if unit == "second":
        if any(o.duration_seconds is None for o in outputs):
            return None
        return float(sum(o.duration_seconds for o in outputs))
    return None


def facts_from_body(body: dict) -> list[OutputFacts]:
    """What can be read about the outputs from fal's result: each image's size, a video's
    duration where the model reports one. Dialects differ, so this looks in the usual
    places and gives up quietly."""
    facts: list[OutputFacts] = []

    def number(value) -> Optional[float]:
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    def one(item) -> None:
        if not isinstance(item, dict):
            return
        width, height = number(item.get("width")), number(item.get("height"))
        facts.append(OutputFacts(int(width) if width else None, int(height) if height else None,
                                 number(item.get("duration"))))

    for key in ("images", "videos"):
        for item in body.get(key) or []:
            one(item)
    for key in ("image", "video"):
        if isinstance(body.get(key), dict):
            one(body[key])
    return facts
