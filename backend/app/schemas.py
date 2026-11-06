"""The response envelope every router returns.

Single resources are `{"data": T}` and lists are `{"data": [...], "total", "limit",
"offset"}`. Errors are `{"detail": {"code", "message"}}` — a machine-readable code so
the caller can branch, and a sentence so it has something to show when it cannot.

The same shape as Notes, GAM and GVC. It matters more here than anywhere: the suite's
callers are mostly those three apps' servers, and a gateway error they can pass through
unchanged is one their frontends already know how to show.
"""

from typing import Generic, List, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class DataResponse(BaseModel, Generic[T]):
    data: T


class ListResponse(BaseModel, Generic[T]):
    data: List[T]
    total: int = 0
    limit: int = 0
    offset: int = 0


class ErrorDetail(BaseModel):
    code: str
    message: str


class ClientConfig(BaseModel):
    """The settings the browser needs, served rather than compiled in.

    Readable by anyone who can reach the app — the endpoint has to be unauthenticated,
    because the sign-in page reads it. Nothing secret goes in this
    model, and a test asserts the field set so adding one is a deliberate act.
    """

    # Where the sign-in page may send someone afterwards (M6): its own origin and these.
    login_redirect_origins: List[str] = []
    # Whether "forgot password" and emailed codes can work at all.
    email_enabled: bool = False


class HealthResponse(BaseModel):
    status: str
    version: str
