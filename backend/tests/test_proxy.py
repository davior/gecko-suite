"""The raw passthrough: Notes' vendor bodies in, Notes' response shapes out."""

import json

import httpx
import pytest

from app.models.usage import UsageEvent
from tests.test_gateway_chat import _frames, _model, _provider, _sse_body

MESSAGES = [{"role": "user", "content": "Hi"}]


def _sse(events):
    return httpx.Response(200, content=_sse_body(events), headers={"content-type": "text/event-stream"})


def _anthropic_events(text="Hello", usage_in=20, usage_out=7, extra=None):
    return [
        {"type": "message_start", "message": {"model": "claude-opus-5-5", "content": [],
                                              "usage": {"input_tokens": usage_in, "output_tokens": 0,
                                                        "cache_read_input_tokens": 4}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text[:2]}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text[2:]}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": usage_out}},
        {"type": "message_stop"},
    ]


@pytest.fixture(name="claude")
def claude_fixture(auth_client, catalog):
    pid = _provider(auth_client)
    _model(auth_client, pid, model_id="claude-opus-5-5")
    return pid


def _anthropic_body(**fields):
    return {"vendor": "anthropic", "model": "claude-opus-5-5", "max_tokens": 500, "messages": MESSAGES, **fields}


def test_a_blocking_anthropic_call_returns_the_reassembled_message(auth_client, claude, vendors, session):
    vendors.respond = lambda r: _sse(_anthropic_events())
    res = auth_client.post("/api/v1/ai/proxy/anthropic", json=_anthropic_body(
        system=[{"type": "text", "text": "Be brief", "cache_control": {"type": "ephemeral"}}],
        prefill="{", app="notes"))
    assert res.status_code == 200, res.text
    message = res.json()
    # The vendor's own dict, with no {"data": ...} envelope.
    assert message["content"] == [{"type": "text", "text": "Hello"}] and message["stop_reason"] == "end_turn"

    sent = json.loads(vendors.calls[-1].content)
    assert sent["stream"] is True and sent["max_tokens"] == 500
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent["messages"][-1] == {"role": "assistant", "content": "{"}
    assert vendors.calls[-1].headers["x-api-key"] == "sk-ant-secret"

    [event] = session.exec(__import__("sqlmodel").select(UsageEvent)).all()
    assert (event.input_tokens, event.output_tokens, event.cache_read_tokens) == (20, 7, 4)
    assert event.app == "notes" and event.model == "claude-opus-5-5" and event.cost is not None


def test_a_stream_is_notes_frames(auth_client, claude, vendors):
    vendors.respond = lambda r: _sse(_anthropic_events())
    res = auth_client.post("/api/v1/ai/proxy/anthropic/stream", json=_anthropic_body())
    assert res.headers["content-type"].startswith("text/event-stream")
    frames = _frames(res.text)
    assert frames[:2] == [("delta", {"text": "He"}), ("delta", {"text": "llo"})]
    kind, final = frames[-1]
    assert kind == "final" and final["content"] == [{"type": "text", "text": "Hello"}]


def test_a_proxied_stream_sends_notes_nothing_for_a_tool_input(auth_client, claude, vendors):
    """Notes reads only delta and final; the progress the gateway turns into keepalives
    is not a frame here."""
    events = [
        {"type": "message_start", "message": {"content": [], "usage": {}}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "tu_1", "name": "x", "input": {}}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": "{}"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
        {"type": "message_stop"},
    ]
    vendors.respond = lambda r: _sse(events)
    res = auth_client.post("/api/v1/ai/proxy/anthropic/stream", json=_anthropic_body())
    frames = _frames(res.text)
    assert [kind for kind, _ in frames] == ["final"]
    assert frames[0][1]["content"][0]["type"] == "tool_use"


def test_a_stream_usage_is_recorded_once(auth_client, claude, vendors, session):
    from sqlmodel import select

    vendors.respond = lambda r: _sse(_anthropic_events())
    auth_client.post("/api/v1/ai/proxy/anthropic/stream", json=_anthropic_body())
    assert len(session.exec(select(UsageEvent)).all()) == 1


def test_web_search_tools_get_the_beta_header_only_when_asked(auth_client, claude, vendors):
    vendors.respond = lambda r: _sse(_anthropic_events())
    auth_client.post("/api/v1/ai/proxy/anthropic", json=_anthropic_body())
    assert "web-search" not in vendors.calls[-1].headers["anthropic-beta"]
    auth_client.post("/api/v1/ai/proxy/anthropic", json=_anthropic_body(
        tools=[{"type": "web_search_20250305", "name": "web_search"}]))
    assert "web-search-2025-03-05" in vendors.calls[-1].headers["anthropic-beta"]
    assert json.loads(vendors.calls[-1].content)["tools"][0]["name"] == "web_search"


def test_a_model_the_suite_was_not_told_about_is_priced_by_family(auth_client, claude, vendors, session):
    from sqlmodel import select

    vendors.respond = lambda r: _sse(_anthropic_events())
    res = auth_client.post("/api/v1/ai/proxy/anthropic", json=_anthropic_body(model="claude-opus-5-9"))
    assert res.status_code == 200
    # No row for it, so the catalogue's family price applies; the name goes to the vendor as sent.
    assert json.loads(vendors.calls[-1].content)["model"] == "claude-opus-5-9"
    [event] = session.exec(select(UsageEvent)).all()
    assert event.cost is not None and event.model == "claude-opus-5-9"


def test_a_model_nothing_prices_has_an_unknown_cost_not_zero(auth_client, catalog, vendors, session):
    from sqlmodel import select

    _provider(auth_client, name="Gateway", vendor="custom", base_url="https://gw.example.com")
    body = {"id": "x", "model": "mystery-1", "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2}}
    vendors.respond = lambda r: httpx.Response(200, json=body)
    res = auth_client.post("/api/v1/ai/proxy/openai", json={
        "vendor": "custom", "model": "mystery-1", "max_tokens": 10, "messages": MESSAGES})
    assert res.status_code == 200
    assert "max_tokens" in json.loads(vendors.calls[-1].content)  # only OpenAI's own takes the new name
    [event] = session.exec(select(UsageEvent)).all()
    assert event.cost is None


def test_the_provider_with_that_model_wins_over_the_first_of_the_vendor(auth_client, catalog, vendors):
    first = _provider(auth_client, name="A", api_key="key-a")
    _model(auth_client, first, model_id="claude-sonnet-5-5")
    second = _provider(auth_client, name="B", api_key="key-b")
    _model(auth_client, second, model_id="claude-opus-5-5")
    vendors.respond = lambda r: _sse(_anthropic_events())
    auth_client.post("/api/v1/ai/proxy/anthropic", json=_anthropic_body())
    assert vendors.calls[-1].headers["x-api-key"] == "key-b"


def test_no_matching_provider_is_a_409_even_when_streaming(auth_client, catalog):
    for path in ("anthropic", "anthropic/stream"):
        res = auth_client.post(f"/api/v1/ai/proxy/{path}", json=_anthropic_body())
        assert res.status_code == 409
        assert res.json()["detail"]["code"] == "ai_not_configured"


def test_another_users_provider_is_not_used(auth_client, claude, switch_user, vendors):
    switch_user("someone-else")
    res = auth_client.post("/api/v1/ai/proxy/anthropic", json=_anthropic_body())
    assert res.status_code == 409


def test_a_vendor_error_mid_stream_is_an_error_frame(auth_client, claude, vendors):
    vendors.respond = lambda r: httpx.Response(401, json={"error": {"message": "bad key"}})
    res = auth_client.post("/api/v1/ai/proxy/anthropic/stream", json=_anthropic_body())
    assert res.status_code == 200
    kind, data = _frames(res.text)[-1]
    assert kind == "error" and data["code"] and "sk-ant-secret" not in res.text


def test_a_blocking_vendor_error_is_the_suites_error_shape(auth_client, claude, vendors):
    vendors.respond = lambda r: httpx.Response(401, json={"error": {"message": "bad key"}})
    res = auth_client.post("/api/v1/ai/proxy/anthropic", json=_anthropic_body())
    assert res.status_code >= 400 and "code" in res.json()["detail"]
    assert "sk-ant-secret" not in res.text


# ─── OpenAI-compatible ───────────────────────────────────────────────────────


@pytest.fixture(name="gpt")
def gpt_fixture(auth_client, catalog):
    pid = _provider(auth_client, name="OpenAI", vendor="openai", api_key="sk-oa")
    _model(auth_client, pid, model_id="gpt-4.1-mini")


def test_a_blocking_openai_call_returns_the_vendors_json(auth_client, gpt, vendors, session):
    from sqlmodel import select

    reply = {"model": "gpt-4.1-mini", "choices": [{"message": {"role": "assistant", "content": "hi"}}],
             "usage": {"prompt_tokens": 100, "completion_tokens": 10}}
    vendors.respond = lambda r: httpx.Response(200, json=reply)
    res = auth_client.post("/api/v1/ai/proxy/openai", json={
        "vendor": "openai", "model": "gpt-4.1-mini", "max_tokens": 50, "messages": MESSAGES})
    assert res.json() == reply
    sent = json.loads(vendors.calls[-1].content)
    assert sent["max_completion_tokens"] == 50 and "stream" not in sent
    assert vendors.calls[-1].headers["authorization"] == "Bearer sk-oa"
    [event] = session.exec(select(UsageEvent)).all()
    assert (event.input_tokens, event.output_tokens) == (100, 10)


def test_an_openai_stream_ends_with_a_final_carrying_usage(auth_client, gpt, vendors):
    chunks = [
        {"model": "gpt-4.1-mini", "choices": [{"delta": {"content": "Hel"}}]},
        {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 8, "completion_tokens": 2}},
    ]
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    vendors.respond = lambda r: httpx.Response(200, content=body.encode(),
                                               headers={"content-type": "text/event-stream"})
    res = auth_client.post("/api/v1/ai/proxy/openai/stream", json={
        "vendor": "openai", "model": "gpt-4.1-mini", "max_tokens": 50, "messages": MESSAGES})
    frames = _frames(res.text)
    assert frames[:2] == [("delta", {"text": "Hel"}), ("delta", {"text": "lo"})]
    kind, final = frames[-1]
    assert kind == "final" and final["choices"][0]["message"]["content"] == "Hello"
    assert final["usage"] == {"prompt_tokens": 8, "completion_tokens": 2}
    assert json.loads(vendors.calls[-1].content)["stream_options"] == {"include_usage": True}


def test_an_anthropic_request_does_not_pick_an_openai_provider(auth_client, gpt):
    res = auth_client.post("/api/v1/ai/proxy/anthropic", json=_anthropic_body())
    assert res.status_code == 409


# ─── Ollama ──────────────────────────────────────────────────────────────────


@pytest.fixture(name="ollama")
def ollama_fixture(auth_client, catalog):
    pid = _provider(auth_client, name="Local", vendor="ollama", api_key="", base_url="http://localhost:11434")
    _model(auth_client, pid, model_id="llama3", capabilities=["chat"])


def test_ollama_blocking_and_streaming(auth_client, ollama, vendors, session):
    from sqlmodel import select

    reply = {"message": {"role": "assistant", "content": "hi"}, "done": True,
             "prompt_eval_count": 9, "eval_count": 3}
    vendors.respond = lambda r: httpx.Response(200, json=reply)
    res = auth_client.post("/api/v1/ai/proxy/ollama", json={"model": "llama3", "messages": MESSAGES, "max_tokens": 40})
    assert res.json() == reply
    sent = json.loads(vendors.calls[-1].content)
    assert str(vendors.calls[-1].url) == "http://localhost:11434/api/chat"
    assert sent["stream"] is False and sent["options"]["num_predict"] == 40

    lines = [{"message": {"content": "h"}, "done": False}, {"message": {"content": "i"}, "done": False},
             {"message": {"content": ""}, "done": True, "done_reason": "stop",
              "prompt_eval_count": 9, "eval_count": 3}]
    vendors.respond = lambda r: httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())
    res = auth_client.post("/api/v1/ai/proxy/ollama/stream", json={"model": "llama3", "messages": MESSAGES})
    frames = _frames(res.text)
    assert frames[:2] == [("delta", {"text": "h"}), ("delta", {"text": "i"})]
    kind, final = frames[-1]
    assert kind == "final" and final["message"]["content"] == "hi" and final["eval_count"] == 3
    events = session.exec(select(UsageEvent)).all()
    assert len(events) == 2 and all(e.cost == 0.0 for e in events)
