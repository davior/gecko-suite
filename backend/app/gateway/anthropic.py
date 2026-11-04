"""The Anthropic Messages protocol: request building, reply parsing, and streaming.

Serves `anthropic`, and `deepseek` or a `custom` gateway with `use_anthropic_api` set.
Raw httpx rather than the Anthropic SDK, as in every app in the suite: one layer speaks
four vendors' protocols, and the compatible gateways are reached at their own URLs with
their own auth headers.

Rules from the current API that shape this (carried from GVC, checked against the
Claude API reference):
- Assistant content goes back exactly as it arrived, thinking blocks included.
- Nothing forces a tool or turns thinking off. On current models a forced `tool_choice`
  and a disabled-thinking setting are both rejected, so the defaults (`auto`, adaptive
  thinking) are left alone and effort is the caller's lever (`output_config.effort`).
- No assistant prefill: current models reject a final assistant message.
"""

from __future__ import annotations

import json
from typing import Any, Iterator, Optional

from app.gateway import upstream
from app.gateway.types import (
    ChatMessage,
    ChatRequest,
    ImagePart,
    RawMessage,
    Reply,
    Target,
    ToolCallOut,
    UpstreamError,
    Usage,
)
from app.providers.endpoints import anthropic_base, anthropic_headers
from app.vocab import VENDOR_ANTHROPIC

PROTOCOL = "anthropic"

# A first draft can be several thousand tokens of tool input, generated before a byte
# comes back on a blocking request.
REQUEST_TIMEOUT = 300.0
# Between events on a stream. Vendors send pings, so a long silence means it is dead.
STREAM_READ_TIMEOUT = 120.0

_STOPS = {
    "end_turn": "end",
    "stop_sequence": "end",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
    "pause_turn": "pause",
}

# Models that get Anthropic's server-side refusal fallback by default: on a safety
# decline the API re-runs the same request on another model inside the same call,
# instead of the caller getting nothing. The reference recommends it for exactly these.
# The served model comes back in the reply's `model`, which is what usage records.
# A model's or assignment's params can turn it off with {"fallbacks": null}.
FALLBACK_MODELS = frozenset({"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"})
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# The web search tool version differs by endpoint: Anthropic's current models take the
# dynamic-filtering variant; DeepSeek's compatible endpoint and unknown gateways get the
# basic one, which is what Notes has always sent them.
WEB_SEARCH_CURRENT = {"type": "web_search_20260209", "name": "web_search"}
WEB_SEARCH_BASIC = {"type": "web_search_20250305", "name": "web_search"}


def _image_block(part: ImagePart) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": part.media_type, "data": part.data}}


def _content_blocks(message: ChatMessage) -> list[dict]:
    """A message as a list of Messages-API content blocks."""
    if message.role == "user":
        if isinstance(message.content, str):
            return [{"type": "text", "text": message.content}]
        blocks = []
        for part in message.content or []:
            blocks.append(_image_block(part) if isinstance(part, ImagePart) else {"type": "text", "text": part.text})
        return blocks
    if message.role == "tool":
        return [
            {"type": "tool_result", "tool_use_id": r.call_id, "content": r.content,
             **({"is_error": True} if r.is_error else {})}
            for r in message.results
        ]
    # assistant
    if message.raw is not None and message.raw.protocol == PROTOCOL:
        content = (message.raw.message or {}).get("content") if isinstance(message.raw.message, dict) else None
        if isinstance(content, list):
            # Verbatim: thinking and fallback blocks included, nothing re-serialised.
            return content
    blocks: list[dict] = []
    if message.text:
        blocks.append({"type": "text", "text": message.text})
    for call in message.tool_calls:
        blocks.append({"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments})
    return blocks


def build_messages(messages: list[ChatMessage]) -> list[dict]:
    """The conversation in Messages-API form.

    Consecutive messages with the same role are merged. Anthropic combines them itself,
    but a compatible gateway may 400 on them instead, and they happen honestly: a turn
    that failed before answering leaves two user messages in a row.
    """
    out: list[dict] = []
    for message in messages:
        role = "assistant" if message.role == "assistant" else "user"
        blocks = _content_blocks(message)
        if not blocks:
            continue
        if out and out[-1]["role"] == role:
            out[-1]["content"] = list(out[-1]["content"]) + list(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})
    return out


def build_body(target: Target, request: ChatRequest, *, stream: bool, max_tokens: int,
               params: dict[str, Any]) -> tuple[dict, dict[str, str]]:
    body: dict[str, Any] = {
        "model": target.model_id,
        "max_tokens": max_tokens,
        "messages": build_messages(request.messages),
    }
    # Each system block is a cache breakpoint. Three at most: Anthropic allows four, and
    # the top-level one below takes the last.
    system = [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}
              for text in request.system[:3] if text]
    # Past three, the rest are joined into the third so nothing is dropped silently.
    if len(request.system) > 3:
        system[-1]["text"] = "\n\n".join(t for t in request.system[2:] if t)
    if system:
        body["system"] = system

    tools: list[dict] = []
    for spec in request.tools:
        tool: dict[str, Any] = {"name": spec.name, "description": spec.description,
                                "input_schema": spec.parameters}
        if stream and target.vendor == VENDOR_ANTHROPIC:
            # Large tool inputs (a whole draft) stream as they are generated instead of
            # arriving in one burst. The cost is that the input is no longer validated
            # by the API, so a cut-off one is caught in `_finish_tool_input`.
            tool["eager_input_streaming"] = True
        tools.append(tool)
    if "web_search" in request.builtin_tools:
        tools.append(dict(WEB_SEARCH_CURRENT if target.vendor == VENDOR_ANTHROPIC else WEB_SEARCH_BASIC))
    if tools:
        body["tools"] = tools

    headers = anthropic_headers(target.api_key, target.vendor)
    if target.vendor == VENDOR_ANTHROPIC:
        # Top-level caching moves its breakpoint to the end of the conversation each
        # call, so a long tool loop re-reads its history at the cached rate. Anthropic
        # only: a compatible gateway may not know the field.
        body["cache_control"] = {"type": "ephemeral"}
        if target.model_id in FALLBACK_MODELS:
            body["fallbacks"] = "default"
            headers["anthropic-beta"] = f"{headers['anthropic-beta']},{FALLBACK_BETA}"

    body.update(params)
    # A params value of null removes a default (e.g. {"fallbacks": null}).
    body = {k: v for k, v in body.items() if v is not None}
    if "fallbacks" not in body and "anthropic-beta" in headers:
        headers["anthropic-beta"] = ",".join(
            b for b in headers["anthropic-beta"].split(",") if b != FALLBACK_BETA
        )
    if stream:
        body["stream"] = True
    return body, headers


def url_for(target: Target) -> str:
    return f"{anthropic_base(_ProviderView(target))}/v1/messages"


class _ProviderView:
    """`endpoints.anthropic_base` reads a provider row; a Target has the same fields."""

    def __init__(self, target: Target):
        self.vendor = target.vendor
        self.base_url = target.base_url
        self.use_anthropic_api = target.use_anthropic_api


def parse_usage(payload: Any) -> Usage:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    return Usage(
        input_tokens=upstream.as_int(usage, "input_tokens"),
        output_tokens=upstream.as_int(usage, "output_tokens"),
        cache_read_tokens=upstream.as_int(usage, "cache_read_input_tokens"),
        cache_write_tokens=upstream.as_int(usage, "cache_creation_input_tokens"),
    )


def parse(payload: Any, target: Target) -> Reply:
    usage = parse_usage(payload)
    served = str(payload.get("model") or target.model_id) if isinstance(payload, dict) else target.model_id
    if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
        raise UpstreamError(f"{target.provider_name} returned a message with no content",
                            usage=usage, served_model=served)

    text_parts: list[str] = []
    calls: list[ToolCallOut] = []
    for block in payload["content"]:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            text_parts.append(block.get("text", ""))
        elif kind == "tool_use":
            arguments = block.get("input")
            error = block.pop("_parse_error", None)
            if error is None and not isinstance(arguments, dict):
                error = "arguments were not an object"
            calls.append(ToolCallOut(
                id=str(block.get("id", "")), name=str(block.get("name", "")),
                arguments=arguments if isinstance(arguments, dict) else {}, parse_error=error,
            ))
        # thinking, server_tool_use, web_search_tool_result, fallback: carried in `raw`.

    stop = _STOPS.get(payload.get("stop_reason"), "other")
    details = payload.get("stop_details") if isinstance(payload.get("stop_details"), dict) else {}
    if stop == "max_tokens" and calls and not calls[-1].parse_error:
        # Only the last block can have been cut off. Its arguments may parse and still
        # be incomplete, so it is never run.
        calls[-1] = calls[-1].model_copy(update={"parse_error": "cut off by max_tokens"})
    return Reply(
        text="".join(text_parts).strip(),
        tool_calls=calls,
        stop=stop,
        usage=usage,
        raw=RawMessage(protocol=PROTOCOL, message={"role": "assistant", "content": payload["content"]}),
        served_model=served,
        refusal_category=details.get("category") if stop == "refusal" else None,
    )


def call(target: Target, request: ChatRequest, *, max_tokens: int, params: dict) -> Reply:
    body, headers = build_body(target, request, stream=False, max_tokens=max_tokens, params=params)
    response = upstream.post_json(url_for(target), headers=headers, body=body,
                                  timeout=REQUEST_TIMEOUT, label=target.provider_name)
    if response.status_code >= 400:
        raise upstream.http_error(response, target.provider_name)
    try:
        payload = response.json()
    except ValueError as exc:
        raise UpstreamError(f"{target.provider_name} returned a response that was not JSON") from exc
    return parse(payload, target)


def _finish_tool_input(block: dict, raw: Optional[str]) -> None:
    """Turn a tool_use block's accumulated partial JSON into its `input`."""
    if raw is None:
        return
    try:
        value = json.loads(raw) if raw else {}
        block["input"] = value
    except json.JSONDecodeError:
        # With eager input streaming the API no longer validates, so a cut-off input
        # arrives as broken JSON. Kept as {} in the history (the API requires an
        # object) and reported, so the call is handed back as an error, never run.
        block["input"] = {}
        block["_parse_error"] = "arguments were not valid JSON"


def stream(target: Target, request: ChatRequest, *, max_tokens: int, params: dict) -> Iterator[tuple[str, Any]]:
    """Yield ("delta", text) as text arrives, then ("final", Reply)."""
    body, headers = build_body(target, request, stream=True, max_tokens=max_tokens, params=params)
    for kind, value in message_events(url_for(target), headers, body, label=target.provider_name):
        if kind == "delta":
            yield kind, value
        else:
            yield "final", parse(value, target)


def message_events(url: str, headers: dict, body: dict, *, label: str,
                   read_timeout: float = STREAM_READ_TIMEOUT) -> Iterator[tuple[str, Any]]:
    """Stream a Messages request: ("delta", text) as text arrives, then ("message", dict).

    Reassembles the same message the blocking endpoint would return (ported from Notes'
    `_iter_anthropic_events`), so `parse` reads both, and so the raw passthrough can hand
    it back as it is. `body` must already say `stream: true`.
    """
    message: dict[str, Any] = {"content": []}
    buffers: dict[int, str] = {}

    with upstream.stream_lines(url, headers=headers, body=body, read_timeout=read_timeout,
                               label=label) as lines:
        for line in lines:
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data:
                continue
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            if kind == "message_start":
                message = event.get("message") or {}
                message.setdefault("content", [])
            elif kind == "content_block_start":
                index = event.get("index", 0)
                content = message.setdefault("content", [])
                while len(content) <= index:
                    content.append({})
                content[index] = dict(event.get("content_block") or {})
            elif kind == "content_block_delta":
                index = event.get("index", 0)
                content = message.get("content", [])
                if index >= len(content):
                    continue
                block, delta = content[index], event.get("delta") or {}
                dtype = delta.get("type")
                if dtype == "text_delta":
                    chunk = delta.get("text", "")
                    block["text"] = block.get("text", "") + chunk
                    if chunk:
                        yield "delta", chunk
                elif dtype == "input_json_delta":
                    buffers[index] = buffers.get(index, "") + delta.get("partial_json", "")
                elif dtype == "thinking_delta":
                    block["thinking"] = block.get("thinking", "") + delta.get("thinking", "")
                elif dtype == "signature_delta":
                    block["signature"] = block.get("signature", "") + delta.get("signature", "")
                elif dtype == "citations_delta" and delta.get("citation") is not None:
                    block.setdefault("citations", []).append(delta["citation"])
            elif kind == "content_block_stop":
                index = event.get("index", 0)
                content = message.get("content", [])
                if index < len(content):
                    _finish_tool_input(content[index], buffers.pop(index, None))
            elif kind == "message_delta":
                message.update(event.get("delta") or {})
                if event.get("usage"):
                    message.setdefault("usage", {}).update(event["usage"])
            elif kind == "message_stop":
                break
            elif kind == "error":
                error = event.get("error") or {}
                raise UpstreamError(
                    f"{label} stream error: {error.get('message') or error.get('type', 'error')}",
                    usage=parse_usage(message), served_model=str(message.get("model") or ""),
                )

    # A stream cut off before content_block_stop leaves partial input behind.
    for index, raw in list(buffers.items()):
        if index < len(message.get("content", [])):
            _finish_tool_input(message["content"][index], raw)
    yield "message", message
