"""A provider's stored `extra_params`, decoded into something safe to merge.

The blob is the user's escape hatch for whatever their model wants — `temperature`,
`top_p`, a vendor-specific knob this app has never heard of — which is the whole point
of it being free-form JSON. What it must never do is reach the keys the client builds
itself: a blob that sets `messages` or `model` does not tune a request, it replaces one.
"""

from __future__ import annotations

import json
from typing import Any, Optional

# Request-body keys the clients construct. Stripped before the merge, so the blob can
# only ever add optional parameters. `stream` is here because the gateway decides
# whether to stream from what the caller asked for, and a blob that turned streaming on
# would hand a non-streaming caller a body it cannot parse.
PROTECTED_KEYS = frozenset(
    {
        "model",
        "max_tokens",
        "messages",
        "system",
        "tools",
        "stream",
        "stream_options",
    }
)


def parse_extra_params(raw: Optional[str]) -> dict[str, Any]:
    """Decode the stored JSON text. Anything unusable reads as "none set".

    Not an error: a blob written by an older version, or by hand, should cost the user a
    tuning parameter rather than the whole turn.
    """
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k not in PROTECTED_KEYS}
