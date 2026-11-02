"""Chat through the suite, in the shape GVC's assistant already uses.

`SuiteChat.conversation(...)` returns an object with GVC's `ask` / `answer` / `tell`, so
an app's existing loop can switch to the suite by changing where its provider comes from.
The conversation keeps the history exactly as the suite returned it, `raw` vendor messages
included, because thinking blocks must be replayed verbatim (see the suite's
`app/gateway/types.py`).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

from gecko_suite_client.client import SuiteClient


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict
    # Set when the model's arguments were unusable (bad JSON, cut off). Answer it with
    # an error result so the model can retry; never execute it.
    parse_error: Optional[str] = None


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False


@dataclass
class Image:
    media_type: str
    data: bytes
    label: str = ""


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    # end | tool_use | max_tokens | refusal | pause | other
    stop: str = "end"
    model: str = ""
    vendor: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost: Optional[float] = None
    refusal_category: Optional[str] = None


class SuiteChat:
    """A chat model reached through the suite, chosen by task routing."""

    def __init__(self, client: SuiteClient, *, task: str = "chat.assistant", app: Optional[str] = None,
                 model_ref: Optional[str] = None, persona_id: Optional[str] = None,
                 params: Optional[dict] = None, subject_ref: Optional[str] = None):
        self.client = client
        self.task = None if model_ref else task
        self.model_ref = model_ref
        self.app = app or client.app
        self.persona_id = persona_id
        self.params = params or {}
        self.subject_ref = subject_ref
        self._resolved: Optional[dict] = None

    def resolve(self) -> Optional[dict]:
        """What the task routes to right now, or None when nothing is routed. An app
        calls this to decide whether to offer an AI feature at all."""
        if self._resolved is None and self.task:
            query = {"task": self.task, **({"app": self.app} if self.app else {})}
            self._resolved = self.client.request("GET", "/api/v1/resolve", params=query).get("model") or {}
        return self._resolved or None

    @property
    def model(self) -> str:
        return (self.resolve() or {}).get("model_id", "")

    @property
    def provider_type(self) -> str:
        return (self.resolve() or {}).get("vendor", "")

    @property
    def supports_images(self) -> bool:
        return "vision" in (self.resolve() or {}).get("capabilities", [])

    def conversation(self, *, system: Sequence[str], tools: Sequence[ToolSpec] = (),
                     history: Sequence[tuple[str, str]] = (), prompt: str = "",
                     images: Sequence[Image] = ()) -> "SuiteConversation":
        """A new conversation, in GVC's ChatProvider shape.

        Images open it, ahead of the history, so they stay in the cached prefix on every
        turn (GVC's arrangement).
        """
        messages: list[dict] = []
        if images:
            parts: list[dict] = []
            for image in images:
                if image.label:
                    parts.append({"type": "text", "text": image.label})
                parts.append({"type": "image", "media_type": image.media_type,
                              "data": base64.b64encode(image.data).decode("ascii")})
            messages.append({"role": "user", "content": parts})
        for role, text in history:
            messages.append({"role": role, "content": text} if role == "user" else {"role": "assistant", "text": text})
        if prompt:
            messages.append({"role": "user", "content": prompt})
        return SuiteConversation(self, list(system), list(tools), messages)

    def request_body(self, system: list[str], tools: list[ToolSpec], messages: list[dict], **extra) -> dict:
        body: dict[str, Any] = {
            "system": system,
            "messages": messages,
            "tools": [{"name": t.name, "description": t.description, "parameters": t.parameters} for t in tools],
            "params": self.params,
            **extra,
        }
        body.update({"task": self.task} if self.task else {"model_ref": self.model_ref})
        for key in ("app", "persona_id", "subject_ref"):
            if getattr(self, key):
                body[key] = getattr(self, key)
        return body


class SuiteConversation:
    """One conversation's history, grown append-only, sent whole on every ask."""

    def __init__(self, chat: SuiteChat, system: list[str], tools: list[ToolSpec], messages: list[dict]):
        self.chat, self.system, self.tools, self.messages = chat, system, tools, messages

    def ask(self, *, on_delta: Optional[Callable[[str], None]] = None, builtin_tools: Sequence[str] = ()) -> Reply:
        """Send the history and return the model's next message.

        With `on_delta`, the call streams and text is handed over as it arrives (for a
        chat panel that should not sit silent through a long generation).
        """
        body = self.chat.request_body(self.system, self.tools, self.messages,
                                      **({"builtin_tools": list(builtin_tools)} if builtin_tools else {}))
        if on_delta is None:
            data = self.chat.client.request("POST", "/api/v1/ai/chat", json=body)
        else:
            data = None
            for event, payload in self.chat.client.stream("/api/v1/ai/chat", {**body, "stream": True}):
                if event == "delta":
                    on_delta(payload.get("text", ""))
                elif event == "final":
                    data = payload
                elif event == "error":
                    from gecko_suite_client.client import SuiteError

                    raise SuiteError(502, payload.get("code", "error"), payload.get("message", "The stream failed"))
            if data is None:
                from gecko_suite_client.client import SuiteUnavailable

                raise SuiteUnavailable(0, "stream_incomplete", "The stream ended without a reply")
        # Verbatim, raw vendor message included: the next ask replays it.
        self.messages.append(data["message"])
        return Reply(
            text=data["text"],
            tool_calls=[ToolCall(c["id"], c["name"], c.get("arguments") or {}, c.get("parse_error"))
                        for c in data["tool_calls"]],
            stop=data["stop"], model=data["model"], vendor=data["vendor"],
            input_tokens=data["usage"]["input_tokens"], output_tokens=data["usage"]["output_tokens"],
            cost=data.get("cost"), refusal_category=data.get("refusal_category"),
        )

    def answer(self, results: Sequence[ToolResult]) -> None:
        """Every result for the last reply's calls, in one message: splitting them teaches
        a model to stop calling tools in parallel."""
        self.messages.append({"role": "tool", "results": [
            {"call_id": r.call_id, "content": r.content, "is_error": r.is_error} for r in results]})

    def tell(self, text: str) -> None:
        """A user message mid-turn: what a loop says when the model stops too soon."""
        self.messages.append({"role": "user", "content": text})
