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
# `max_uses` is per call. Five is what Notes always gave its native search, and what the
# apps cap their own web_search tool at in a turn.
WEB_SEARCH_MAX_USES = 5
WEB_SEARCH_CURRENT = {"type": "web_search_20260209", "name": "web_search", "max_uses": WEB_SEARCH_MAX_USES}
WEB_SEARCH_BASIC = {"type": "web_search_20250305", "name": "web_search", "max_uses": WEB_SEARCH_MAX_USES}

_SEARCH_BLOCKS = ("server_tool_use", "web_search_tool_result")

# ─── a turn DeepSeek leaves open ─────────────────────────────────────────────
#
# Ported from Notes' `assistant/continuation.py`, where the reasoning is written out in
# full. Anthropic's own search completes inside one call. DeepSeek's Anthropic endpoint
# does not: once the model has spent `max_uses` its reply stops with
# `stop_reason: "tool_use"` and every outstanding call is a `server_tool_use` DeepSeek
# already answered. Nothing is left for a caller to run, so an app's agent loop would end
# the turn on the model's between-search commentary ("I'll write the note now…"). The
# gateway finishes it instead: what the model has so far goes back as plain text, with
# the hits, and the model is asked to carry on without the search tool.

# A provider that stalls twice in a row will stall forever, and each attempt is a full
# round trip on an already slow turn.
MAX_STALL_CONTINUATIONS = 2
# It says the budget is spent, because a model whose last searches failed with
# `max_uses_exceeded` otherwise just tries to search again. The caller's own tools stay
# offered (Notes' planner records its changes through them), so it may still call those.
FINISH_TURN_REQUEST = (
    "Your web searching for this turn is over: the search budget is spent and no further "
    "searches are available. Do not describe what you are about to do and do not apologise. "
    "Carry on with the request above now, using the other tools you were given if it needs them."
)
# Enough hits per search to cite from, few enough that several do not crowd the context.
REPLAYED_HITS_PER_SEARCH = 5


def _image_block(part: ImagePart) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": part.media_type, "data": part.data}}


def search_summary(blocks: list[dict]) -> str:
    """The searches in a run of content blocks, as text: each query and its first hits.

    The query is on a `server_tool_use` block, the hits on the `web_search_tool_result`
    that follows it.
    """
    searches: list[str] = []
    query = ""
    for block in blocks:
        if block.get("type") == "server_tool_use":
            asked = block.get("input").get("query") if isinstance(block.get("input"), dict) else None
            query = asked if isinstance(asked, str) else ""
        elif block.get("type") == "web_search_tool_result":
            hits = block.get("content") if isinstance(block.get("content"), list) else []
            lines = []
            for hit in hits:
                url = hit.get("url") if isinstance(hit, dict) else None
                if not isinstance(url, str) or not url:
                    continue
                title = hit.get("title")
                lines.append(f"- {title if isinstance(title, str) and title else url} — {url}")
                if len(lines) == REPLAYED_HITS_PER_SEARCH:
                    break
            if lines:
                searches.append("\n".join([f"Search “{query}”:", *lines]))
    if not searches:
        return ""
    return "\n\n".join(["Web search results gathered this turn:", *searches])


def fold_searches(content: list[dict]) -> list[dict]:
    """Assistant content with each run of search blocks replaced by a text summary.

    For a compatible gateway only. Replaying `server_tool_use` / `web_search_tool_result`
    pairs verbatim is exactly what a compatible-but-not-identical endpoint validates
    differently, and Notes never sent them back to DeepSeek; a summary keeps what the
    model learned without the risk. Anthropic gets them verbatim, as its API expects.
    """
    out: list[dict] = []
    run: list[dict] = []

    def flush() -> None:
        summary = search_summary(run)
        if summary:
            out.append({"type": "text", "text": summary})
        run.clear()

    for block in content:
        if isinstance(block, dict) and block.get("type") in _SEARCH_BLOCKS:
            run.append(block)
            continue
        flush()
        out.append(block)
    flush()
    return out


def is_stalled(payload: Any) -> bool:
    """A reply that stopped on tool use with nothing left for the caller to run."""
    if not isinstance(payload, dict) or payload.get("stop_reason") != "tool_use":
        return False
    blocks = [b for b in payload.get("content") or [] if isinstance(b, dict)]
    return (any(b.get("type") == "server_tool_use" for b in blocks)
            and not any(b.get("type") == "tool_use" for b in blocks))


def _stalled_as_text(payload: dict) -> str:
    blocks = [b for b in payload.get("content") or [] if isinstance(b, dict)]
    parts = [t for t in ((b.get("text") or "").strip() for b in blocks if b.get("type") == "text") if t]
    summary = search_summary(blocks)
    if summary:
        parts.append(summary)
    # An assistant turn may not be empty, and a model that searched without writing a
    # word leaves nothing else to send back.
    return "\n\n".join(parts) or "(searching)"


def continuation(body: dict, payload: dict) -> dict:
    """The follow-up request that finishes a stalled turn: the original body untouched,
    so its cache breakpoints still hit, plus two messages, without the search tool."""
    nxt = dict(body)
    nxt["messages"] = [
        *body["messages"],
        {"role": "assistant", "content": [{"type": "text", "text": _stalled_as_text(payload)}]},
        {"role": "user", "content": [{"type": "text", "text": FINISH_TURN_REQUEST}]},
    ]
    tools = [t for t in body.get("tools") or [] if not str(t.get("type", "")).startswith("web_search")]
    if tools:
        nxt["tools"] = tools
    else:
        nxt.pop("tools", None)
    return nxt


def _with_gathered(payload: dict, stalled: list[dict]) -> dict:
    """The final reply, carrying what the stalled rounds found, for the caller's history.

    The caller replays only this message, so without it the next round would have lost
    the hits. Placed after any leading thinking blocks, which must come first.
    """
    summary = "\n\n".join(s for s in (search_summary(
        [b for b in p.get("content") or [] if isinstance(b, dict)]) for p in stalled) if s)
    if not summary:
        return payload
    content = list(payload.get("content") or [])
    at = 0
    while at < len(content) and isinstance(content[at], dict) and content[at].get("type") in (
            "thinking", "redacted_thinking"):
        at += 1
    content.insert(at, {"type": "text", "text": summary})
    return {**payload, "content": content}


def _add_usage(total: Usage, more: Optional[Usage]) -> Usage:
    if more is None:
        return total
    return Usage(input_tokens=total.input_tokens + more.input_tokens,
                 output_tokens=total.output_tokens + more.output_tokens,
                 cache_read_tokens=total.cache_read_tokens + more.cache_read_tokens,
                 cache_write_tokens=total.cache_write_tokens + more.cache_write_tokens)


def _content_blocks(message: ChatMessage, *, fold: bool = False) -> list[dict]:
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
            return fold_searches(content) if fold else content
    blocks: list[dict] = []
    if message.text:
        blocks.append({"type": "text", "text": message.text})
    for call in message.tool_calls:
        blocks.append({"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments})
    return blocks


def build_messages(messages: list[ChatMessage], *, fold_searches: bool = False) -> list[dict]:
    """The conversation in Messages-API form.

    Consecutive messages with the same role are merged. Anthropic combines them itself,
    but a compatible gateway may 400 on them instead, and they happen honestly: a turn
    that failed before answering leaves two user messages in a row.
    """
    out: list[dict] = []
    for message in messages:
        role = "assistant" if message.role == "assistant" else "user"
        blocks = _content_blocks(message, fold=fold_searches)
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
        "messages": build_messages(request.messages, fold_searches=target.vendor != VENDOR_ANTHROPIC),
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


def _post(target: Target, body: dict, headers: dict) -> dict:
    response = upstream.post_json(url_for(target), headers=headers, body=body,
                                  timeout=REQUEST_TIMEOUT, label=target.provider_name)
    if response.status_code >= 400:
        raise upstream.http_error(response, target.provider_name)
    try:
        return response.json()
    except ValueError as exc:
        raise UpstreamError(f"{target.provider_name} returned a response that was not JSON") from exc


def _finish(payload: Any, target: Target, stalled: list[dict], spent: Usage) -> Reply:
    """Parse the last reply, counting the stalled rounds' tokens and keeping their hits."""
    reply = parse(payload, target)
    reply.usage = _add_usage(spent, reply.usage)
    if stalled:
        # Into the history only, after parsing, so the text the app shows is the answer.
        reply.raw = RawMessage(protocol=PROTOCOL, message=_with_gathered(reply.raw.message, stalled))
    return reply


def _continued(exc: UpstreamError, spent: Usage) -> UpstreamError:
    # The stalled rounds were billed even though the turn then failed.
    exc.usage = _add_usage(spent, exc.usage)
    return exc


def call(target: Target, request: ChatRequest, *, max_tokens: int, params: dict) -> Reply:
    body, headers = build_body(target, request, stream=False, max_tokens=max_tokens, params=params)
    payload = _post(target, body, headers)
    stalled: list[dict] = []
    spent = Usage()
    while is_stalled(payload) and len(stalled) < MAX_STALL_CONTINUATIONS:
        stalled.append(payload)
        spent = _add_usage(spent, parse_usage(payload))
        body = continuation(body, payload)
        try:
            payload = _post(target, body, headers)
        except UpstreamError as exc:
            raise _continued(exc, spent) from exc
    return _finish(payload, target, stalled, spent)


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
    """Yield ("delta", text) as text arrives, ("progress", None) for other output, then
    ("final", Reply)."""
    body, headers = build_body(target, request, stream=True, max_tokens=max_tokens, params=params)
    stalled: list[dict] = []
    spent = Usage()
    while True:
        message: Any = None
        try:
            for kind, value in message_events(url_for(target), headers, body, label=target.provider_name):
                if kind in ("delta", "progress"):
                    yield kind, value
                else:
                    message = value
        except UpstreamError as exc:
            raise _continued(exc, spent) from exc
        if not is_stalled(message) or len(stalled) >= MAX_STALL_CONTINUATIONS:
            yield "final", _finish(message, target, stalled, spent)
            return
        stalled.append(message)
        spent = _add_usage(spent, parse_usage(message))
        body = continuation(body, message)
        # The commentary already streamed stays on screen; the answer follows it.
        yield "delta", "\n\n"


def message_events(url: str, headers: dict, body: dict, *, label: str,
                   read_timeout: float = STREAM_READ_TIMEOUT) -> Iterator[tuple[str, Any]]:
    """Stream a Messages request: ("delta", text) as text arrives, then ("message", dict).

    ("progress", None) marks output that is not text (a tool's input, thinking), so a
    caller can show the stream is alive through minutes of a large tool call.

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
                    yield "progress", None
                elif dtype == "thinking_delta":
                    block["thinking"] = block.get("thinking", "") + delta.get("thinking", "")
                    yield "progress", None
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
