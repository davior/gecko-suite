"""Refuse a user-supplied URL that points back inside the network.

Anywhere this app is handed a URL and later fetches it, the user chooses the
destination — so "the internet" is not the only place it can reach. A base URL of
`https://169.254.169.254/` or `https://10.0.0.5:8080/` turns an outbound request into a
read of whatever the container can see that the browser cannot.

Copied from GVC (from GAM, which ported it from gecko-notes' `_require_safe_external_url` and
added the DNS check). Kept at the app root rather than inside `app/providers/`: it is not
about providers.

Two exceptions are deliberate and live at the call sites, not here:
- Ollama's base URL is allowed to be private. It is a local daemon; that is the point.
- `anthropic` and `deepseek` resolve to fixed managed endpoints, so their stored
  base_url is never fetched at all (see app.providers.endpoints).
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.parse

from fastapi import HTTPException, status


def _reject(code: str, message: str) -> None:
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": code, "message": message},
    )


def _is_safe_address(host: str) -> bool:
    """Whether a literal IP is routable on the public internet.

    `is_global` is False for loopback, link-local, private and reserved ranges, which is
    exactly the set worth refusing, and it stays correct for IPv6 without a second list.
    """
    return ipaddress.ip_address(host).is_global


def require_safe_external_url(url: str) -> None:
    """Raise unless `url` is https and resolves somewhere public."""
    parsed = urllib.parse.urlparse(url)

    if parsed.scheme != "https":
        _reject("invalid_url", "The address must start with https://")

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        _reject("invalid_url", "That address has no hostname")

    try:
        if not _is_safe_address(hostname):
            _reject("ssrf_blocked", "The address must point to a public host")
        return
    except ValueError:
        pass  # a name, not a literal address — resolve it below

    if hostname == "localhost" or hostname.endswith(".localhost"):
        _reject("ssrf_blocked", "The address must point to a public host")

    # gecko-notes stops at the literal-IP check, which leaves the interesting case open:
    # a name the attacker controls that resolves to 127.0.0.1 passes it untouched. So
    # resolve and check every address the name answers with.
    try:
        resolved = socket.getaddrinfo(hostname, None)
    except (socket.gaierror, UnicodeError):
        # Deliberately not a rejection. A name that cannot be resolved here may resolve
        # perfectly well later (split-horizon DNS, a temporarily unreachable resolver),
        # and refusing to *save* a setting because of a transient lookup failure is a
        # worse failure than letting the eventual request fail on its own merits. This
        # is defence in depth, not the only line of it.
        return

    for family, _type, _proto, _canonname, sockaddr in resolved:
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        if not _is_safe_address(sockaddr[0]):
            _reject("ssrf_blocked", "The address must point to a public host")
