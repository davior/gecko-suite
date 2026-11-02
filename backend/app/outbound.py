"""The one place outbound HTTP clients are built.

Every call the suite makes to a vendor goes through `client()`, so tests swap
`TRANSPORT` for an `httpx.MockTransport` and fake every vendor without a network. No test
in this repository reaches the internet.
"""

from typing import Dict, Optional

import httpx

# Tests set this; production leaves it None, which is httpx's real network transport.
TRANSPORT: Optional[httpx.BaseTransport] = None


def client(*, timeout: float | httpx.Timeout, headers: Optional[Dict[str, str]] = None) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        headers=headers or {},
        transport=TRANSPORT,
        # Never follow a redirect. A vendor endpoint that redirects is misconfigured, and
        # following one would carry the API key to wherever it points.
        follow_redirects=False,
    )
