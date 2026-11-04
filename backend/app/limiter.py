"""Request rate limits for the sign-in routes (M6), as gecko-notes has them.

Keyed by the client address, which ProxyHeadersMiddleware has already taken from the
reverse proxy's X-Forwarded-For. In-process memory: one backend container, and a limit
that resets on a restart is still a limit on guessing passwords.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
