"""The OpenAI chat-completions protocol: request building, reply parsing, and streaming.

Serves `openai`, `custom`, `deepseek` (unless pointed at its Anthropic endpoint) and
`ollama`, whose `/v1` endpoint speaks the same protocol, tools included. Ported from
GVC's `providers/openai.py`; streaming from Notes' `_iter_openai_events`, extended to
reassemble tool calls, which Notes' version (having no tools) drops.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

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
from app.providers.endpoints import openai_compat_base
from app.vocab import VENDOR_OLLAMA, VENDOR_OPENAI

PROTOCOL = "openai"

REQUEST_TIMEOUT = 300.0
# A local model on modest hardware takes minutes per answer, not seconds.
OLLAMA_TIMEOUT = 900.0
STREAM_READ_TIMEOUT = 120.0

_STOPS = {
    "stop": "end",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
}


def _user_content(message: ChatMessage) -> Any:
    if isinstance(message.content, str):
        return message.content
    parts = []
    for part in message.content or []:
        if isinstance(part, ImagePart):
            # Data URLs: what OpenAI and Ollama's /v1 both read.
            parts.append({"type": "image_url",
                          "image_url": {"url": f"data:{part.media_type};base64,{part.data}"}})
        else:
            parts.append({"type": "text", "text": part.text})
    return parts


def build_messages(system: list[str], messages: list[ChatMessage]) -> list[dict]:
    out: list[dict] = []
    # One system message: this protocol has no blocks, and its vendors cache the longest
    # stable prefix on their own.
    joined = "\n\n".join(text for text in system if text)
    if joined:
        out.append({"role": "system", "content": joined})
    for message in messages:
        if message.role == "user":
            out.append({"role": "user", "content": _user_content(message)})
        elif message.role == "tool":
            for r in message.results:
                # No error flag in this protocol: an error is said in the content.
                out.append({"role": "tool", "tool_call_id": r.call_id,
                            "content": ("ERROR: " + r.content) if r.is_error else r.content})
        elif message.raw is not None and message.raw.protocol == PROTOCOL and isinstance(message.raw.message, dict):
            out.append(message.raw.message)
        else:
            echoed: dict[str, Any] = {"role": "assistant", "content": message.text or None}
            if message.tool_calls:
                echoed["tool_calls"] = [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                    for c in message.tool_calls
                ]
            out.append(echoed)
    return out


def _url_and_timeout(target: Target) -> tuple[str, float]:
    base = openai_compat_base(target.vendor, target.base_url)
    timeout = OLLAMA_TIMEOUT if target.vendor == VENDOR_OLLAMA else REQUEST_TIMEOUT
    return f"{base}/v1/chat/completions", timeout


def build_body(target: Target, request: ChatRequest, *, stream: bool, max_tokens: int,
               params: dict[str, Any]) -> tuple[dict, dict[str, str]]:
    body: dict[str, Any] = {
        "model": target.model_id,
        "messages": build_messages(request.system, request.messages),
    }
    # OpenAI's own reasoning models reject `max_tokens` and take
    # `max_completion_tokens`, which its other chat models also accept. The compatible
    # servers (DeepSeek, Ollama, most gateways) know only `max_tokens`.
    body["max_completion_tokens" if target.vendor == VENDOR_OPENAI else "max_tokens"] = max_tokens
    if request.tools:
        body["tools"] = [
            {"type": "function",
             "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
            for t in request.tools
        ]
    body.update(params)
    body = {k: v for k, v in body.items() if v is not None}
    if stream:
        body["stream"] = True
        # Without this a streamed completion reports no token counts at all.
        body["stream_options"] = {"include_usage": True}
    headers = {"content-type": "application/json"}
    if target.api_key:
        headers["Authorization"] = f"Bearer {target.api_key}"
    return body, headers


def parse_usage(payload: Any) -> Usage:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    prompt = upstream.as_int(usage, "prompt_tokens")
    completion = upstream.as_int(usage, "completion_tokens")
    if not (prompt or completion):
        # Some servers report only a total. Counted as input, the cheaper side, so the
        # estimate errs low rather than inventing spend.
        prompt = upstream.as_int(usage, "total_tokens")
    details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
    cached = min(upstream.as_int(details, "cached_tokens"), prompt)
    # OpenAI counts cached tokens inside prompt_tokens; split them out so each side is
    # priced at its own rate.
    return Usage(input_tokens=prompt - cached, output_tokens=completion, cache_read_tokens=cached)


def parse(payload: Any, target: Target) -> Reply:
    usage = parse_usage(payload)
    served = str(payload.get("model") or target.model_id) if isinstance(payload, dict) else target.model_id
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise UpstreamError(f"{target.provider_name} returned no choices", usage=usage, served_model=served)
    first = choices[0]
    message = first.get("message") if isinstance(first.get("message"), dict) else {}

    calls: list[ToolCallOut] = []
    for raw in message.get("tool_calls") or []:
        function = (raw or {}).get("function") or {}
        try:
            arguments = json.loads(function.get("arguments") or "{}")
            error = None if isinstance(arguments, dict) else "arguments were not an object"
        except ValueError:
            arguments, error = {}, "arguments were not valid JSON"
        calls.append(ToolCallOut(id=str(raw.get("id", "")), name=str(function.get("name", "")),
                                 arguments=arguments if isinstance(arguments, dict) else {},
                                 parse_error=error))

    stop = _STOPS.get(first.get("finish_reason"), "other")
    if calls and stop == "end":
        # Some compatible servers say "stop" even when they called a tool.
        stop = "tool_use"
    if stop == "max_tokens" and calls and not calls[-1].parse_error:
        calls[-1] = calls[-1].model_copy(update={"parse_error": "cut off by max_tokens"})

    text = message.get("content")
    # What goes back into the history: role, content and tool calls, nothing else a
    # server might reject on the way back in.
    echoed: dict[str, Any] = {"role": "assistant", "content": message.get("content")}
    if message.get("tool_calls"):
        echoed["tool_calls"] = message["tool_calls"]
    return Reply(
        text=text.strip() if isinstance(text, str) else "",
        tool_calls=calls,
        stop=stop,
        usage=usage,
        raw=RawMessage(protocol=PROTOCOL, message=echoed),
        served_model=served,
    )


def call(target: Target, request: ChatRequest, *, max_tokens: int, params: dict) -> Reply:
    body, headers = build_body(target, request, stream=False, max_tokens=max_tokens, params=params)
    url, timeout = _url_and_timeout(target)
    response = upstream.post_json(url, headers=headers, body=body, timeout=timeout, label=target.provider_name)
    if response.status_code >= 400:
        raise upstream.http_error(response, target.provider_name)
    try:
        payload = response.json()
    except ValueError as exc:
        raise UpstreamError(f"{target.provider_name} returned a response that was not JSON") from exc
    return parse(payload, target)


def stream(target: Target, request: ChatRequest, *, max_tokens: int, params: dict) -> Iterator[tuple[str, Any]]:
    """Yield ("delta", text) as text arrives, then ("final", Reply).

    Tool calls arrive as fragments keyed by `index`: the first carries the id and name,
    later ones append to `arguments`. They are joined here into the blocking shape, so
    `parse` reads both.
    """
    body, headers = build_body(target, request, stream=True, max_tokens=max_tokens, params=params)
    url, _ = _url_and_timeout(target)
    text = ""
    finish_reason = None
    usage: dict = {}
    served = ""
    calls: dict[int, dict] = {}

    with upstream.stream_lines(url, headers=headers, body=body, read_timeout=STREAM_READ_TIMEOUT,
                               label=target.provider_name) as lines:
        for line in lines:
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            served = event.get("model") or served
            if event.get("usage"):
                usage = event["usage"]
            for choice in event.get("choices") or []:
                delta = (choice or {}).get("delta") or {}
                piece = delta.get("content")
                if piece:
                    text += piece
                    yield "delta", piece
                for fragment in delta.get("tool_calls") or []:
                    slot = calls.setdefault(fragment.get("index", 0),
                                            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    if fragment.get("id"):
                        slot["id"] = fragment["id"]
                    function = fragment.get("function") or {}
                    if function.get("name"):
                        slot["function"]["name"] += function["name"]
                    if function.get("arguments"):
                        slot["function"]["arguments"] += function["arguments"]
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]

    message: dict[str, Any] = {"role": "assistant", "content": text or None}
    if calls:
        message["tool_calls"] = [calls[i] for i in sorted(calls)]
    payload = {"model": served or target.model_id, "usage": usage,
               "choices": [{"message": message, "finish_reason": finish_reason}]}
    yield "final", parse(payload, target)
