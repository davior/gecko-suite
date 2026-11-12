"""The shape of a chat request and reply, the same whichever vendor answers it.

The gateway is stateless: every request carries the whole conversation. That rules out
GVC's approach of keeping each conversation in the vendor's own wire format inside a
long-lived object. But the reason GVC did that still holds: Anthropic's current models
return thinking blocks that must be replayed exactly as they came, and a history rebuilt
from a neutral format would drop or alter them.

So every assistant reply carries `raw`: the vendor's own message, verbatim, stamped with
the protocol it is in. A caller appends the reply to its history as-is and sends it back;
the adapter for that protocol replays `raw` untouched and ignores the neutral fields. If
the next request goes to a different protocol (routing changed mid-conversation), `raw`
is ignored and the neutral `text` / `tool_calls` are used instead, which loses only what
the other vendor could not have read anyway.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

_CONFIG = ConfigDict(protected_namespaces=())


# ─── request ─────────────────────────────────────────────────────────────────


class TextPart(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImagePart(BaseModel):
    """A picture, base64-encoded. JPEG, PNG, WebP or GIF: what every vision model reads."""

    type: Literal["image"] = "image"
    media_type: Literal["image/jpeg", "image/png", "image/webp", "image/gif"]
    data: str


ContentPart = Union[TextPart, ImagePart]


class ToolCallIn(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResultIn(BaseModel):
    call_id: str
    content: str
    is_error: bool = False


class RawMessage(BaseModel):
    """A vendor's own assistant message, carried back verbatim (see the module doc)."""

    protocol: Literal["anthropic", "openai"]
    message: Any


class ChatMessage(BaseModel):
    """One turn. Which fields apply depends on the role:

    - user: `content`, a string or a list of text and image parts.
    - assistant: `text` and `tool_calls`, plus `raw` when it came from this gateway.
    - tool: `results`, every result for the previous assistant turn's calls, together.
      All of them in one message: splitting them teaches a model to stop calling tools
      in parallel.
    """

    model_config = _CONFIG

    role: Literal["user", "assistant", "tool"]
    content: Union[str, list[ContentPart], None] = None
    text: Optional[str] = None
    tool_calls: list[ToolCallIn] = Field(default_factory=list)
    raw: Optional[RawMessage] = None
    results: list[ToolResultIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _fields_match_role(self) -> "ChatMessage":
        if self.role == "user" and not self.content:
            raise ValueError("A user message needs content")
        if self.role == "tool" and not self.results:
            raise ValueError("A tool message needs results")
        return self

    def images(self) -> list[ImagePart]:
        if isinstance(self.content, list):
            return [p for p in self.content if isinstance(p, ImagePart)]
        return []


class ToolSpecIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    # JSON Schema of the arguments.
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class ChatRequest(BaseModel):
    """One model call.

    Name a `task` (routed, the usual case), a `model_ref` (the suite's id for a model
    the caller can see), or a `vendor` and `model` name: how Notes' assistant names the
    model the person picked in its own panel, resolved as the raw passthrough resolves it
    (see `gateway/raw.find_by_name`). `app` and `subject_ref` attribute the spend; `app`
    also picks app-specific routing.
    """

    model_config = _CONFIG

    task: Optional[str] = None
    model_ref: Optional[str] = None
    vendor: Optional[str] = Field(default=None, max_length=40)
    model: Optional[str] = Field(default=None, max_length=200)
    app: Optional[str] = None
    subject_ref: Optional[str] = Field(default=None, max_length=300)
    # A persona puts its system prompt first and, if it has one for this task, uses its
    # own model instead of the caller's routing.
    persona_id: Optional[str] = None

    # Most stable first. A protocol with prompt caching breaks after each block, so
    # what never changes goes first and what changes per turn goes last.
    system: list[str] = Field(default_factory=list)
    messages: list[ChatMessage] = Field(min_length=1)
    tools: list[ToolSpecIn] = Field(default_factory=list)
    # Tools the vendor runs itself. Only "web_search", and only on a model with the
    # web_search capability (Anthropic-protocol providers).
    builtin_tools: list[Literal["web_search"]] = Field(default_factory=list)
    # Tools the caller offers itself but would rather the vendor ran. When the model the
    # request resolves to can run one natively (`endpoints.searches_natively`), the
    # caller's tool of that name is dropped and the vendor's is used; otherwise the
    # request goes as sent. Only the suite can decide this, because only it knows which
    # model a task, a persona or a by-name request lands on.
    prefer_builtin: list[Literal["web_search"]] = Field(default_factory=list)

    max_tokens: Optional[int] = Field(default=None, ge=1, le=1_000_000)
    # Extra request parameters (e.g. {"output_config": {"effort": "low"}}), merged over
    # the provider's, model's and assignment's. Structural keys are stripped.
    params: dict[str, Any] = Field(default_factory=dict)
    stream: bool = False

    @model_validator(mode="after")
    def _one_target(self) -> "ChatRequest":
        by_name = bool(self.vendor) or bool(self.model)
        if by_name and not (self.vendor and self.model):
            raise ValueError("A vendor and a model name go together")
        if [bool(self.task), bool(self.model_ref), by_name].count(True) != 1:
            raise ValueError("Name exactly one of task, model_ref, or vendor and model")
        return self

    def has_images(self) -> bool:
        return any(m.images() for m in self.messages)


# ─── reply ───────────────────────────────────────────────────────────────────


class ToolCallOut(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]
    # Set when the model's arguments were not a JSON object (or were cut off). Hand it
    # back as an error result so the model can try again; never execute it.
    parse_error: Optional[str] = None


class UsageOut(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class AssistantMessageOut(BaseModel):
    """What the caller appends to its history, unchanged, for the next request."""

    role: Literal["assistant"] = "assistant"
    text: str
    tool_calls: list[ToolCallOut]
    raw: RawMessage


class ChatResult(BaseModel):
    model_config = _CONFIG

    text: str
    tool_calls: list[ToolCallOut]
    # end | tool_use | max_tokens | refusal | pause | other. `pause` means a server-side
    # tool paused the turn: send the history back as it is to let it continue.
    stop: str
    refusal_category: Optional[str] = None
    message: AssistantMessageOut
    usage: UsageOut
    # The model that answered, which can differ from the one asked for when the vendor
    # fell back to another model after a refusal.
    model: str
    model_ref: str
    provider: str
    vendor: str
    task: Optional[str] = None
    # The vendor-run tools this call had, after `prefer_builtin` was applied.
    builtin_tools: list[str] = Field(default_factory=list)
    cost: Optional[float] = None
    currency: Optional[str] = None
    cost_estimated: bool = True
    usage_event_id: str


# ─── internal ────────────────────────────────────────────────────────────────


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class Reply:
    """What an adapter makes of one vendor response."""

    text: str
    tool_calls: list[ToolCallOut]
    stop: str
    usage: Usage
    raw: RawMessage
    served_model: str = ""
    refusal_category: Optional[str] = None


class UpstreamError(Exception):
    """The vendor refused, failed, or answered with something unusable.

    Carries whatever usage the vendor reported, so a reply that arrived and was billed
    but could not be parsed is still counted. (GAM loses those tokens today.)
    """

    def __init__(self, message: str, *, status: int = 502, code: str = "upstream_error",
                 usage: Optional[Usage] = None, served_model: str = ""):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.usage = usage
        self.served_model = served_model


@dataclass
class Target:
    """Everything an adapter needs about where a request goes, copied out of the ORM
    rows before any network call, so a streamed response never touches a session that
    the request has already closed."""

    provider_id: str
    provider_name: str
    vendor: str
    base_url: Optional[str]
    use_anthropic_api: bool
    api_key: str
    model_ref: str
    model_id: str
    capabilities: set[str]
    max_tokens: Optional[int]
    # Merged in order: provider, model, assignment. The request's own come last.
    params: dict[str, Any] = field(default_factory=dict)
