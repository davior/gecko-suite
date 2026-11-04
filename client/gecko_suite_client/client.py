"""The HTTP client, its two ways of authenticating, and the errors apps branch on."""

from __future__ import annotations

from typing import Any, Iterator, Optional

import httpx

# A chat call can generate for minutes before it answers. Connect and write stay short,
# so a suite that is down fails fast and the app can degrade.
DEFAULT_TIMEOUT = httpx.Timeout(600.0, connect=10.0, write=30.0, pool=10.0)


class SuiteError(Exception):
    """The suite answered with an error. `code` is its snake_case error code."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class SuiteNotConfigured(SuiteError):
    """No model is routed for the task, or its provider has no key. The remedy is a
    setting in the suite: an app treats this as "no AI for this", not as a failure."""


class SuiteUnavailable(SuiteError):
    """The suite could not be reached. Apps degrade, they don't crash."""


_NOT_CONFIGURED = frozenset({"ai_not_configured", "provider_key_missing", "search_unconfigured"})


class SuiteClient:
    """One connection to the suite, as a user or as an app on a user's behalf.

    `token` forwards the signed-in user's JWT, which is what a request handler has. The
    app credential (`app`, `app_secret`, `on_behalf_of`) is for work that outlives the
    request: a background job holds no token.

    `http` lets a test hand in any httpx.Client, including Starlette's TestClient, so
    the whole path from this package to the suite's routes can run in one process.
    """

    def __init__(self, base_url: str = "", *, token: Optional[str] = None, app: Optional[str] = None,
                 app_secret: Optional[str] = None, on_behalf_of: Optional[str] = None,
                 http: Optional[httpx.Client] = None, transport: Optional[httpx.BaseTransport] = None,
                 timeout: httpx.Timeout = DEFAULT_TIMEOUT):
        if bool(token) == bool(app_secret):
            raise ValueError("Give either a user token or an app credential")
        if app_secret and not (app and on_behalf_of):
            raise ValueError("An app credential needs the app's name and the user it acts for")
        self.app = app
        self.headers: dict[str, str] = (
            {"Authorization": f"Bearer {token}"} if token
            else {"X-Gecko-App": app, "X-Gecko-App-Secret": app_secret, "X-On-Behalf-Of": on_behalf_of}
        )
        self._http = http or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport,
                                          follow_redirects=False)

    def _url(self, path: str) -> str:
        return path if path.startswith("/") else f"/{path}"

    def request(self, method: str, path: str, **kwargs) -> Any:
        """Call the suite and return the envelope's `data`, or raise a SuiteError."""
        # A caller's own headers (a raw body's Content-Type) go with the credential's, never
        # in place of them.
        headers = {**kwargs.pop("headers", {}), **self.headers}
        try:
            response = self._http.request(method, self._url(path), headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise SuiteUnavailable(0, "suite_unavailable", f"Gecko Suite could not be reached ({type(exc).__name__})") from exc
        return _data(response)

    def stream(self, path: str, json: dict) -> Iterator[tuple[str, dict]]:
        """POST and yield (event, data) for each Server-Sent Event."""
        import json as jsonlib

        try:
            with self._http.stream("POST", self._url(path), headers=self.headers, json=json) as response:
                if response.status_code >= 400:
                    response.read()
                    _data(response)
                event = None
                for line in response.iter_lines():
                    if line.startswith("event: "):
                        event = line[7:]
                    elif line.startswith("data: ") and event:
                        yield event, jsonlib.loads(line[6:])
                        event = None
        except httpx.HTTPError as exc:
            raise SuiteUnavailable(0, "suite_unavailable", f"Gecko Suite could not be reached ({type(exc).__name__})") from exc

    def close(self) -> None:
        self._http.close()


def _data(response: httpx.Response) -> Any:
    try:
        body = response.json()
    except ValueError:
        body = None
    if response.status_code < 400:
        return body.get("data") if isinstance(body, dict) else body
    detail = body.get("detail") if isinstance(body, dict) else None
    code = detail.get("code", "error") if isinstance(detail, dict) else "error"
    message = detail.get("message", f"HTTP {response.status_code}") if isinstance(detail, dict) else f"HTTP {response.status_code}"
    if code in _NOT_CONFIGURED:
        raise SuiteNotConfigured(response.status_code, code, message)
    if response.status_code in (502, 503, 504) and code in ("error", "suite_unavailable"):
        raise SuiteUnavailable(response.status_code, code, message)
    raise SuiteError(response.status_code, code, message)
