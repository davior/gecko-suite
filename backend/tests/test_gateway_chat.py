"""The chat gateway against faked vendors: what goes out, what comes back, what it costs."""

import json

import httpx
import pytest

from tests.conftest import ADMIN_ID, TEST_USER_ID


def _provider(client, **fields):
    body = {"name": "Claude", "vendor": "anthropic", "api_key": "sk-ant-secret", **fields}
    res = client.post("/api/v1/providers", json=body)
    assert res.status_code == 201, res.text
    return res.json()["data"]["id"]


def _model(client, provider_id, **fields):
    res = client.post(f"/api/v1/providers/{provider_id}/models", json=fields)
    assert res.status_code == 201, res.text
    return res.json()["data"]["id"]


def _route(client, task, model_ref, **fields):
    res = client.put("/api/v1/assignments", json={"task": task, "model_ref": model_ref, **fields})
    assert res.status_code == 200, res.text


@pytest.fixture(name="claude")
def claude_fixture(auth_client, catalog):
    model = _model(auth_client, _provider(auth_client), model_id="claude-opus-5-5")
    _route(auth_client, "chat.assistant", model)
    return model


def _anthropic_reply(content, stop="end_turn", usage=None, **extra):
    return httpx.Response(200, json={
        "model": "claude-opus-5-5", "content": content, "stop_reason": stop,
        "usage": usage or {"input_tokens": 10, "output_tokens": 5}, **extra,
    })


def _chat(client, **fields):
    body = {"task": "chat.assistant", "messages": [{"role": "user", "content": "Hi"}], **fields}
    return client.post("/api/v1/ai/chat", json=body)


TOOL = {"name": "search_assets", "description": "Search the library",
        "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}


# ─── Anthropic ───────────────────────────────────────────────────────────────


def test_an_anthropic_call_end_to_end(auth_client, claude, vendors, session):
    from app.models.usage import UsageEvent

    thinking = {"type": "thinking", "thinking": "", "signature": "sig-abc"}
    vendors.respond = lambda r: _anthropic_reply(
        [thinking, {"type": "text", "text": "Searching."},
         {"type": "tool_use", "id": "tu_1", "name": "search_assets", "input": {"q": "sunset"}}],
        stop="tool_use",
        usage={"input_tokens": 1000, "output_tokens": 500, "cache_read_input_tokens": 2000},
    )
    res = _chat(auth_client, system=["reference", "context"], tools=[TOOL], app="gam",
                subject_ref="gam:asset:42")
    assert res.status_code == 200, res.text
    data = res.json()["data"]

    sent = json.loads(vendors.calls[-1].content)
    headers = vendors.calls[-1].headers
    assert str(vendors.calls[-1].url) == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == "sk-ant-secret"
    assert sent["system"] == [
        {"type": "text", "text": "reference", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "context", "cache_control": {"type": "ephemeral"}},
    ]
    assert sent["cache_control"] == {"type": "ephemeral"}
    assert sent["tools"][0]["input_schema"] == TOOL["parameters"]
    # Nothing forces a tool or switches thinking off: both 400 on current models.
    assert "tool_choice" not in sent and "thinking" not in sent
    # Refusal fallback on by default for the models the reference names.
    assert sent["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in headers["anthropic-beta"]
    assert sent["max_tokens"] == 16_000

    assert data["stop"] == "tool_use"
    assert data["tool_calls"] == [{"id": "tu_1", "name": "search_assets",
                                   "arguments": {"q": "sunset"}, "parse_error": None}]
    assert data["message"]["raw"]["protocol"] == "anthropic"
    assert data["message"]["raw"]["message"]["content"][0] == thinking
    # (1000 * 4 + 500 * 20 + 2000 * 0.20) / 1e6: cache reads at their own price.
    assert data["cost"] == pytest.approx(0.0144)
    assert "sk-ant-secret" not in res.text

    event = session.get(UsageEvent, data["usage_event_id"])
    assert (event.user_id, event.app, event.task, event.subject_ref) == (
        TEST_USER_ID, "gam", "chat.assistant", "gam:asset:42")
    assert (event.input_tokens, event.cache_read_tokens, event.status) == (1000, 2000, "ok")


def test_the_reply_replays_verbatim_with_tool_results_in_one_message(auth_client, claude, vendors):
    thinking = {"type": "thinking", "thinking": "", "signature": "sig-abc"}
    vendors.respond = lambda r: _anthropic_reply(
        [thinking, {"type": "tool_use", "id": "tu_1", "name": "search_assets", "input": {}},
         {"type": "tool_use", "id": "tu_2", "name": "search_assets", "input": {}}], stop="tool_use")
    first = _chat(auth_client, tools=[TOOL]).json()["data"]

    vendors.respond = lambda r: _anthropic_reply([{"type": "text", "text": "Done"}])
    history = [
        {"role": "user", "content": "Hi"},
        first["message"],
        {"role": "tool", "results": [
            {"call_id": "tu_1", "content": "3 hits"},
            {"call_id": "tu_2", "content": "boom", "is_error": True},
        ]},
    ]
    assert _chat(auth_client, tools=[TOOL], messages=history).json()["data"]["text"] == "Done"

    sent = json.loads(vendors.calls[-1].content)["messages"]
    assert sent[1]["role"] == "assistant" and sent[1]["content"][0] == thinking
    assert sent[2] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "tu_1", "content": "3 hits"},
        {"type": "tool_result", "tool_use_id": "tu_2", "content": "boom", "is_error": True},
    ]}


def test_a_refusal_is_a_reply_not_an_error(auth_client, claude, vendors):
    vendors.respond = lambda r: _anthropic_reply(
        [], stop="refusal", stop_details={"type": "refusal", "category": "cyber"})
    data = _chat(auth_client).json()["data"]
    assert (data["stop"], data["refusal_category"]) == ("refusal", "cyber")


def test_a_fallback_answer_is_recorded_and_priced_as_the_model_that_served(auth_client, claude, vendors, session):
    from app.models.usage import UsageEvent

    vendors.respond = lambda r: httpx.Response(200, json={
        "model": "claude-opus-4-8", "stop_reason": "end_turn",
        "content": [{"type": "text", "text": "ok"}],
        "usage": {"input_tokens": 1_000_000, "output_tokens": 0},
    })
    data = _chat(auth_client).json()["data"]
    assert data["model"] == "claude-opus-4-8"
    assert data["cost"] == pytest.approx(5.0)  # Opus 4.8's $5/M, not Opus 5.5's $4/M
    assert session.get(UsageEvent, data["usage_event_id"]).model == "claude-opus-4-8"


def test_params_layer_and_can_remove_a_default_but_never_restructure(auth_client, claude, vendors):
    vendors.respond = lambda r: _anthropic_reply([{"type": "text", "text": "ok"}])
    _chat(auth_client, params={
        "output_config": {"effort": "low"}, "fallbacks": None,
        "model": "claude-haiku-4-5", "messages": [], "stream": True,
    })
    sent = json.loads(vendors.calls[-1].content)
    assert sent["output_config"] == {"effort": "low"}
    assert sent["model"] == "claude-opus-5-5" and sent["messages"]
    assert "stream" not in sent
    assert "fallbacks" not in sent
    assert "server-side-fallback" not in vendors.calls[-1].headers["anthropic-beta"]


def test_web_search_is_offered_as_a_builtin(auth_client, claude, vendors):
    vendors.respond = lambda r: _anthropic_reply([{"type": "text", "text": "ok"}])
    _chat(auth_client, builtin_tools=["web_search"])
    assert json.loads(vendors.calls[-1].content)["tools"] == [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}]


# ─── an app's web_search tool, swapped for the model's own search ────────────

APP_SEARCH = {"name": "web_search", "description": "Search the web.",
              "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}

SEARCHED = [
    {"type": "server_tool_use", "id": "srv_1", "name": "web_search", "input": {"query": "owl facts"}},
    {"type": "web_search_tool_result", "tool_use_id": "srv_1", "content": [
        {"type": "web_search_result", "title": "Owls", "url": "https://owls.example", "encrypted_content": "x"}]},
]


@pytest.fixture(name="deepseek")
def deepseek_fixture(auth_client, catalog):
    pid = _provider(auth_client, name="DeepSeek", vendor="deepseek", api_key="sk-ds", use_anthropic_api=True)
    model = _model(auth_client, pid, model_id="deepseek-chat")
    _route(auth_client, "chat.assistant", model)
    return model


def test_a_claude_model_searches_for_itself_instead_of_calling_the_apps_tool(auth_client, claude, vendors):
    vendors.respond = lambda r: _anthropic_reply([{"type": "text", "text": "ok"}])
    data = _chat(auth_client, tools=[TOOL, APP_SEARCH], prefer_builtin=["web_search"]).json()["data"]
    tools = json.loads(vendors.calls[-1].content)["tools"]
    assert [t["name"] for t in tools] == ["search_assets", "web_search"]
    assert tools[1] == {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}
    assert data["builtin_tools"] == ["web_search"]


def test_deepseek_on_its_anthropic_endpoint_searches_for_itself(auth_client, deepseek, vendors):
    vendors.respond = lambda r: _anthropic_reply([{"type": "text", "text": "ok"}])
    data = _chat(auth_client, tools=[APP_SEARCH], prefer_builtin=["web_search"]).json()["data"]
    assert str(vendors.calls[-1].url) == "https://api.deepseek.com/anthropic/v1/messages"
    assert json.loads(vendors.calls[-1].content)["tools"] == [
        {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]
    assert data["builtin_tools"] == ["web_search"]


def test_a_model_without_its_own_search_keeps_the_apps_tool(auth_client, catalog, vendors):
    # DeepSeek's OpenAI-compatible endpoint has no server-side search.
    pid = _provider(auth_client, name="DeepSeek", vendor="deepseek", api_key="sk-ds")
    _route(auth_client, "chat.assistant", _model(auth_client, pid, model_id="deepseek-chat"))
    vendors.respond = lambda r: httpx.Response(200, json={
        "model": "deepseek-chat", "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    data = _chat(auth_client, tools=[APP_SEARCH], prefer_builtin=["web_search"]).json()["data"]
    assert [t["function"]["name"] for t in json.loads(vendors.calls[-1].content)["tools"]] == ["web_search"]
    assert data["builtin_tools"] == []


def test_a_stalled_deepseek_search_turn_is_finished_by_the_gateway(auth_client, deepseek, vendors, session):
    from app.models.usage import UsageEvent

    script = iter([
        _anthropic_reply([{"type": "text", "text": "Let me look."}, *SEARCHED], stop="tool_use"),
        _anthropic_reply([{"type": "text", "text": "Owls are birds."},
                          {"type": "tool_use", "id": "tu_1", "name": "search_assets", "input": {"q": "owl"}}],
                         stop="tool_use"),
    ])
    vendors.respond = lambda r: next(script)
    data = _chat(auth_client, tools=[TOOL, APP_SEARCH], prefer_builtin=["web_search"]).json()["data"]

    assert len(vendors.calls) == 2
    second = json.loads(vendors.calls[1].content)
    folded, finish = second["messages"][-2:]
    assert folded["role"] == "assistant"
    assert "Let me look." in folded["content"][0]["text"]
    assert "Owls — https://owls.example" in folded["content"][0]["text"]
    assert finish["role"] == "user" and "search budget is spent" in finish["content"][0]["text"]
    # The search that stalled is withheld; the app's own tools are not.
    assert [t["name"] for t in second["tools"]] == ["search_assets"]

    assert data["text"] == "Owls are birds." and data["stop"] == "tool_use"
    assert [c["name"] for c in data["tool_calls"]] == ["search_assets"]
    # The hits ride along in the message the app replays, so the next round has them.
    assert "https://owls.example" in data["message"]["raw"]["message"]["content"][0]["text"]
    assert data["usage"]["input_tokens"] == 20 and data["usage"]["output_tokens"] == 10
    assert len(session.exec(__import__("sqlmodel").select(UsageEvent)).all()) == 1


def test_a_provider_that_keeps_stalling_is_asked_only_twice_more(auth_client, deepseek, vendors):
    vendors.respond = lambda r: _anthropic_reply([*SEARCHED], stop="tool_use")
    data = _chat(auth_client, tools=[APP_SEARCH], prefer_builtin=["web_search"]).json()["data"]
    assert len(vendors.calls) == 3 and data["stop"] == "tool_use" and data["tool_calls"] == []


def test_search_blocks_are_replayed_to_deepseek_as_text_and_to_claude_verbatim(auth_client, claude, catalog, vendors):
    history = [
        {"role": "user", "content": "Owls?"},
        {"role": "assistant", "text": "", "raw": {"protocol": "anthropic", "message": {
            "role": "assistant", "content": [*SEARCHED, {"type": "tool_use", "id": "tu_1", "name": "search_assets",
                                                          "input": {}}]}}},
        {"role": "tool", "results": [{"call_id": "tu_1", "content": "none"}]},
    ]
    vendors.respond = lambda r: _anthropic_reply([{"type": "text", "text": "ok"}])
    _chat(auth_client, messages=history, tools=[TOOL])
    assert json.loads(vendors.calls[-1].content)["messages"][1]["content"][:2] == SEARCHED

    pid = _provider(auth_client, name="DeepSeek", vendor="deepseek", api_key="sk-ds", use_anthropic_api=True)
    _route(auth_client, "chat.assistant", _model(auth_client, pid, model_id="deepseek-chat"))
    _chat(auth_client, messages=history, tools=[TOOL])
    replayed = json.loads(vendors.calls[-1].content)["messages"][1]["content"]
    assert [b["type"] for b in replayed] == ["text", "tool_use"]
    assert "https://owls.example" in replayed[0]["text"]


# ─── OpenAI-compatible ───────────────────────────────────────────────────────


@pytest.fixture(name="gpt")
def gpt_fixture(auth_client, catalog):
    model = _model(auth_client, _provider(auth_client, name="OpenAI", vendor="openai"),
                   model_id="gpt-4.1-mini")
    _route(auth_client, "chat.assistant", model)
    return model


def test_an_openai_call_with_tools(auth_client, gpt, vendors):
    vendors.respond = lambda r: httpx.Response(200, json={
        "model": "gpt-4.1-mini",
        "choices": [{"finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "search_assets", "arguments": '{"q": "dog"}'}}]}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 100,
                  "prompt_tokens_details": {"cached_tokens": 400}},
    })
    data = _chat(auth_client, system=["a", "b"], tools=[TOOL]).json()["data"]

    sent = json.loads(vendors.calls[-1].content)
    assert str(vendors.calls[-1].url) == "https://api.openai.com/v1/chat/completions"
    assert sent["messages"][0] == {"role": "system", "content": "a\n\nb"}
    assert sent["max_completion_tokens"] == 16_000 and "max_tokens" not in sent
    assert sent["tools"][0]["function"]["parameters"] == TOOL["parameters"]
    assert data["tool_calls"][0]["arguments"] == {"q": "dog"}
    assert data["usage"] == {"input_tokens": 600, "output_tokens": 100,
                             "cache_read_tokens": 400, "cache_write_tokens": 0}


def test_broken_tool_arguments_are_flagged_never_passed_on(auth_client, gpt, vendors):
    vendors.respond = lambda r: httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls",
        "message": {"role": "assistant", "tool_calls": [
            {"id": "c1", "function": {"name": "search_assets", "arguments": '{"q": '}}]}}]})
    call = _chat(auth_client, tools=[TOOL]).json()["data"]["tool_calls"][0]
    assert call["arguments"] == {} and call["parse_error"]


def test_switching_protocol_mid_conversation_uses_the_neutral_fields(auth_client, gpt, vendors):
    anthropic_turn = {
        "role": "assistant", "text": "Looking", "tool_calls": [{"id": "tu_1", "name": "search_assets", "arguments": {"q": "x"}}],
        "raw": {"protocol": "anthropic", "message": {"role": "assistant", "content": [{"type": "thinking"}]}},
    }
    vendors.respond = lambda r: httpx.Response(200, json={"choices": [{"finish_reason": "stop",
        "message": {"role": "assistant", "content": "ok"}}]})
    _chat(auth_client, tools=[TOOL], messages=[
        {"role": "user", "content": "Hi"}, anthropic_turn,
        {"role": "tool", "results": [{"call_id": "tu_1", "content": "none", "is_error": True}]},
    ])
    sent = json.loads(vendors.calls[-1].content)["messages"]
    assert sent[1] == {"role": "assistant", "content": "Looking", "tool_calls": [
        {"id": "tu_1", "type": "function", "function": {"name": "search_assets", "arguments": '{"q": "x"}'}}]}
    assert sent[2] == {"role": "tool", "tool_call_id": "tu_1", "content": "ERROR: none"}


def test_ollama_needs_no_key_and_costs_nothing(auth_client, catalog, vendors):
    pid = _provider(auth_client, name="Local", vendor="ollama", api_key="", base_url="http://localhost:11434")
    _route(auth_client, "chat.assistant", _model(auth_client, pid, model_id="llama3", capabilities=["chat"]))
    vendors.respond = lambda r: httpx.Response(200, json={"choices": [{"finish_reason": "stop",
        "message": {"role": "assistant", "content": "hi"}}], "usage": {"prompt_tokens": 9, "completion_tokens": 3}})
    data = _chat(auth_client).json()["data"]
    assert str(vendors.calls[-1].url) == "http://localhost:11434/v1/chat/completions"
    assert "authorization" not in vendors.calls[-1].headers
    assert "max_tokens" in json.loads(vendors.calls[-1].content)
    assert data["cost"] == 0.0


# ─── refusals before sending ─────────────────────────────────────────────────


def test_nothing_routed_is_a_409_the_app_can_show(auth_client):
    res = _chat(auth_client)
    assert res.status_code == 409 and res.json()["detail"]["code"] == "ai_not_configured"


def test_images_to_a_blind_model_are_refused_before_sending(auth_client, catalog, vendors):
    pid = _provider(auth_client, name="DeepSeek", vendor="deepseek")
    _route(auth_client, "chat.assistant", _model(auth_client, pid, model_id="deepseek-chat"))
    res = _chat(auth_client, messages=[{"role": "user", "content": [
        {"type": "image", "media_type": "image/png", "data": "iVBORw0KGgo="},
        {"type": "text", "text": "What is this?"}]}])
    assert res.status_code == 400 and "cannot see images" in res.json()["detail"]["message"]
    assert vendors.calls == []


def test_tools_to_a_model_without_them_are_refused(auth_client, catalog, vendors):
    pid = _provider(auth_client, name="DeepSeek", vendor="deepseek")
    _route(auth_client, "chat.assistant", _model(auth_client, pid, model_id="deepseek-reasoner"))
    assert _chat(auth_client, tools=[TOOL]).status_code == 400
    assert vendors.calls == []


def test_another_users_model_cannot_be_named(auth_client, claude, switch_user):
    switch_user("someone-else")
    res = auth_client.post("/api/v1/ai/chat", json={
        "model_ref": claude, "messages": [{"role": "user", "content": "Hi"}]})
    assert res.status_code == 404


def test_a_request_names_exactly_one_target(auth_client, claude):
    res = auth_client.post("/api/v1/ai/chat", json={
        "task": "chat.assistant", "model_ref": claude, "messages": [{"role": "user", "content": "x"}]})
    assert res.status_code == 422


# ─── vendor failures ─────────────────────────────────────────────────────────


def test_a_rejected_key_is_a_502_that_says_so_without_the_key(auth_client, claude, vendors):
    vendors.respond = lambda r: httpx.Response(401, json={"error": {"message": "invalid x-api-key"}})
    res = _chat(auth_client)
    assert res.status_code == 502
    assert res.json()["detail"]["code"] == "upstream_auth"
    assert "sk-ant-secret" not in res.text


def test_a_billed_reply_that_cannot_be_parsed_is_still_counted(auth_client, claude, vendors, session):
    from sqlmodel import select

    from app.models.usage import UsageEvent

    vendors.respond = lambda r: httpx.Response(200, json={
        "model": "claude-opus-5-5", "usage": {"input_tokens": 50, "output_tokens": 7}})
    assert _chat(auth_client).status_code == 502
    event = session.exec(select(UsageEvent)).one()
    assert (event.status, event.input_tokens, event.output_tokens) == ("error", 50, 7)


def test_rate_limits_are_retried_then_reported(auth_client, claude, vendors, monkeypatch):
    from app.gateway import upstream

    monkeypatch.setattr(upstream.time, "sleep", lambda s: None)
    vendors.respond = lambda r: httpx.Response(429, json={"error": {"message": "slow down"}})
    res = _chat(auth_client)
    assert len(vendors.calls) == upstream.MAX_ATTEMPTS
    assert res.status_code == 429 and res.json()["detail"]["code"] == "upstream_rate_limited"


# ─── streaming ───────────────────────────────────────────────────────────────


def _sse_body(events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def _frames(text):
    frames = []
    for chunk in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in chunk.split("\n"))
        frames.append((lines["event"], json.loads(lines["data"])))
    return frames


def test_an_anthropic_stream_with_a_tool_call(auth_client, claude, vendors, session):
    from app.models.usage import UsageEvent

    events = [
        {"type": "message_start", "message": {"model": "claude-opus-5-5", "content": [],
                                              "usage": {"input_tokens": 20, "output_tokens": 0}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hel"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "lo"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "tool_use", "id": "tu_1", "name": "search_assets", "input": {}}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"q": '}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '"cat"}'}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 12}},
        {"type": "message_stop"},
    ]
    vendors.respond = lambda r: httpx.Response(200, content=_sse_body(events),
                                               headers={"content-type": "text/event-stream"})
    res = _chat(auth_client, tools=[TOOL], stream=True)
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/event-stream")

    frames = _frames(res.text)
    assert [f for f in frames if f[0] == "delta"] == [("delta", {"text": "Hel"}), ("delta", {"text": "lo"})]
    assert ("tool_call", {"id": "tu_1", "name": "search_assets", "arguments": {"q": "cat"},
                          "parse_error": None}) in frames
    kind, final = frames[-1]
    assert kind == "final" and final["text"] == "Hello" and final["stop"] == "tool_use"

    sent = json.loads(vendors.calls[-1].content)
    assert sent["stream"] is True and sent["max_tokens"] == 64_000
    assert sent["tools"][0]["eager_input_streaming"] is True
    event = session.get(UsageEvent, final["usage_event_id"])
    assert (event.input_tokens, event.output_tokens) == (20, 12)


def test_a_cut_off_streamed_tool_input_is_flagged(auth_client, claude, vendors):
    events = [
        {"type": "message_start", "message": {"content": [], "usage": {}}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "tu_1", "name": "search_assets", "input": {}}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"q": "ca'}},
        {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}},
        {"type": "message_stop"},
    ]
    vendors.respond = lambda r: httpx.Response(200, content=_sse_body(events))
    final = _frames(_chat(auth_client, tools=[TOOL], stream=True).text)[-1][1]
    assert final["tool_calls"][0]["parse_error"]
    # The history keeps an object: the API rejects anything else on replay.
    assert final["message"]["raw"]["message"]["content"][0]["input"] == {}
    assert "_parse_error" not in final["message"]["raw"]["message"]["content"][0]


def test_an_openai_stream_reassembles_tool_call_fragments(auth_client, gpt, vendors):
    chunks = [
        {"model": "gpt-4.1-mini", "choices": [{"delta": {"content": "On it"}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "search_assets", "arguments": '{"q"'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ': "owl"}'}}]}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 30, "completion_tokens": 8}},
    ]
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    vendors.respond = lambda r: httpx.Response(200, content=body.encode())
    frames = _frames(_chat(auth_client, tools=[TOOL], stream=True).text)
    final = frames[-1][1]
    assert final["text"] == "On it"
    assert final["tool_calls"][0]["arguments"] == {"q": "owl"}
    assert final["usage"]["input_tokens"] == 30
    sent = json.loads(vendors.calls[-1].content)
    assert sent["stream_options"] == {"include_usage": True}


def test_a_vendor_failure_mid_stream_is_an_error_frame(auth_client, claude, vendors):
    vendors.respond = lambda r: httpx.Response(500, json={"error": {"message": "overloaded"}})
    res = _chat(auth_client, stream=True)
    assert res.status_code == 200
    assert _frames(res.text) == [("error", {"code": "upstream_error", "message": "Claude: overloaded"})]


def test_configuration_mistakes_are_http_errors_even_when_streaming(auth_client):
    assert _chat(auth_client, stream=True).status_code == 409


# ─── usage report ────────────────────────────────────────────────────────────


def test_the_usage_report_counts_unpriced_calls_separately(auth_client, claude, catalog, vendors, switch_user, admin):
    vendors.respond = lambda r: _anthropic_reply([{"type": "text", "text": "ok"}],
                                                 usage={"input_tokens": 1_000_000, "output_tokens": 0})
    _chat(auth_client, app="gam")
    pid = _provider(auth_client, name="Gateway", vendor="custom", base_url="https://gw.example.com")
    _route(auth_client, "chat.quick", _model(auth_client, pid, model_id="mystery", capabilities=["chat"]))
    vendors.respond = lambda r: httpx.Response(200, json={"choices": [{"finish_reason": "stop",
        "message": {"role": "assistant", "content": "ok"}}], "usage": {"prompt_tokens": 5, "completion_tokens": 5}})
    _chat(auth_client, task="chat.quick", app="notes")

    report = auth_client.get("/api/v1/usage").json()["data"]
    assert report["totals"]["events"] == 2 and report["totals"]["priced_events"] == 1
    assert report["totals"]["cost"] == pytest.approx(4.0)
    assert {g["key"]["app"]: g["events"] for g in report["by_app"]} == {"gam": 1, "notes": 1}
    from app.clock import utcnow

    assert [(g["key"]["day"], g["events"]) for g in report["by_day"]] == [
        (utcnow().date().isoformat(), 2)]
    gam_only = auth_client.get("/api/v1/usage?app=gam").json()["data"]
    assert gam_only["totals"]["events"] == 1

    switch_user("someone-else")
    assert auth_client.get("/api/v1/usage").json()["data"]["totals"]["events"] == 0
    assert auth_client.get("/api/v1/usage?scope=all").status_code == 403
    switch_user(ADMIN_ID)
    assert auth_client.get("/api/v1/usage?scope=all").json()["data"]["totals"]["events"] == 2


def test_a_stalled_streamed_turn_is_finished_in_the_same_stream(auth_client, deepseek, vendors):
    def streamed(content, stop):
        events = [{"type": "message_start", "message": {"model": "deepseek-chat", "content": [],
                                                        "usage": {"input_tokens": 10, "output_tokens": 0}}}]
        for i, block in enumerate(content):
            if block["type"] == "text":
                events += [{"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}},
                           {"type": "content_block_delta", "index": i,
                            "delta": {"type": "text_delta", "text": block["text"]}}]
            else:
                events.append({"type": "content_block_start", "index": i, "content_block": block})
            events.append({"type": "content_block_stop", "index": i})
        events += [{"type": "message_delta", "delta": {"stop_reason": stop}, "usage": {"output_tokens": 5}},
                   {"type": "message_stop"}]
        return httpx.Response(200, content=_sse_body(events), headers={"content-type": "text/event-stream"})

    script = iter([streamed([{"type": "text", "text": "Looking."}, *SEARCHED], "tool_use"),
                   streamed([{"type": "text", "text": "Owls are birds."}], "end_turn")])
    vendors.respond = lambda r: next(script)
    frames = _frames(_chat(auth_client, tools=[APP_SEARCH], prefer_builtin=["web_search"], stream=True).text)

    assert len(vendors.calls) == 2
    deltas = "".join(f[1]["text"] for f in frames if f[0] == "delta")
    assert deltas == "Looking.\n\nOwls are birds."
    final = frames[-1][1]
    assert frames[-1][0] == "final" and final["text"] == "Owls are birds." and final["stop"] == "end"
    assert final["usage"]["input_tokens"] == 20
