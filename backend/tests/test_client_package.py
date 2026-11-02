"""gecko-suite-client against the real suite, in one process: the package's requests go
through Starlette's TestClient to the actual routes, and the vendors are faked.

This is the test that proves an app switching to the suite gets what it had before:
GVC's ask/answer/tell shape, verbatim replay, and the agent loop.
"""

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "client"))

from gecko_suite_client import (  # noqa: E402
    SuiteChat,
    SuiteClient,
    SuiteEmbedder,
    SuiteError,
    SuiteNotConfigured,
    SuiteUnavailable,
    ToolSpec,
    run_agent,
    web_search,
)

from tests.conftest import ADMIN_ID, TEST_USER_ID  # noqa: E402

SEARCH = ToolSpec("search_assets", "Search the library",
                  {"type": "object", "properties": {"q": {"type": "string"}}})


def _setup_claude(auth_client):
    pid = auth_client.post("/api/v1/providers", json={"name": "Claude", "vendor": "anthropic", "api_key": "k"}).json()["data"]["id"]
    mid = auth_client.post(f"/api/v1/providers/{pid}/models", json={"model_id": "claude-opus-5-5"}).json()["data"]["id"]
    auth_client.put("/api/v1/assignments", json={"task": "chat.assistant", "model_ref": mid})


def _reply(content, stop="end_turn"):
    return httpx.Response(200, json={"model": "claude-opus-5-5", "content": content, "stop_reason": stop,
                                     "usage": {"input_tokens": 100, "output_tokens": 10}})


@pytest.fixture(name="suite")
def suite_fixture(auth_client, catalog):
    _setup_claude(auth_client)
    # The token is irrelevant here (auth_client signs in by override); what is under
    # test is everything the package does on the wire.
    return SuiteClient(token="user-jwt", http=auth_client)


def test_an_agent_turn_runs_the_tool_and_replays_verbatim(suite, vendors):
    thinking = {"type": "thinking", "thinking": "", "signature": "sig"}
    script = iter([
        _reply([thinking, {"type": "tool_use", "id": "t1", "name": "search_assets", "input": {"q": "owls"}}], "tool_use"),
        _reply([{"type": "text", "text": "Found 3 owls."}]),
    ])
    vendors.respond = lambda r: next(script)
    seen = []
    chat = SuiteChat(suite, app="gam")
    conversation = chat.conversation(system=["ref"], tools=[SEARCH], prompt="Find owls")
    outcome = run_agent(conversation, handlers={"search_assets": lambda args: seen.append(args) or "3 hits"})

    assert outcome.reply == "Found 3 owls." and outcome.cut_short is None and outcome.rounds == 2
    assert seen == [{"q": "owls"}]
    second = json.loads(vendors.calls[1].content)["messages"]
    assert second[1]["content"][0] == thinking
    assert second[2]["content"] == [{"type": "tool_result", "tool_use_id": "t1", "content": "3 hits"}]
    assert outcome.cost == pytest.approx(2 * (100 * 4 + 10 * 20) / 1e6)


def test_the_loop_answers_every_call_even_when_it_cannot_run_it(suite, vendors):
    script = iter([
        _reply([{"type": "tool_use", "id": "a", "name": "nope", "input": {}},
                {"type": "tool_use", "id": "b", "name": "search_assets", "input": {}},
                {"type": "tool_use", "id": "c", "name": "search_assets", "input": {}}], "tool_use"),
        _reply([{"type": "text", "text": "ok"}]),
    ])
    vendors.respond = lambda r: next(script)

    def boom(args):
        raise RuntimeError("index offline")

    conversation = SuiteChat(suite).conversation(system=[], tools=[SEARCH], prompt="x")
    run_agent(conversation, handlers={"search_assets": boom}, caps={"search_assets": 1})
    results = json.loads(vendors.calls[1].content)["messages"][-1]["content"]
    assert [r["is_error"] for r in results] == [True, True, True]
    assert "no tool called nope" in results[0]["content"]
    assert "index offline" in results[1]["content"]
    assert "limit of 1" in results[2]["content"]


def test_a_nudge_keeps_the_model_going_a_bounded_number_of_times(suite, vendors):
    vendors.respond = lambda r: _reply([{"type": "text", "text": "done?"}])
    conversation = SuiteChat(suite).conversation(system=[], prompt="x")
    steps = []
    outcome = run_agent(conversation, nudge=lambda reply: "Still 1 issue: fix it.", max_nudges=2,
                        on_step=lambda kind, text: steps.append(kind))
    assert outcome.rounds == 3
    assert steps == []  # reporting a nudge is the app's call: it knows what to say
    assert conversation.messages[-2] == {"role": "user", "content": "Still 1 issue: fix it."}


def test_an_apps_own_conversation_works_without_on_delta_or_cost():
    """GVC's conversations (and its scripted test ones) have ask(), answer() and tell() and
    nothing more; the loop must not pass what they do not take."""
    from gecko_suite_client import ToolCall, ToolResult
    from gecko_suite_client.chat import Reply

    class OwnConversation:
        def __init__(self):
            self.script = [
                Reply(text="", tool_calls=[ToolCall("a", "search_assets", {})], stop="tool_use"),
                Reply(text="", stop="max_tokens"),
            ]
            self.told, self.answered = [], []

        def ask(self):
            return self.script.pop(0)

        def answer(self, results):
            self.answered.append(results)

        def tell(self, text):
            self.told.append(text)

    conversation = OwnConversation()
    steps = []
    outcome = run_agent(
        conversation, handlers={"search_assets": lambda args: "ok"}, on_step=lambda k, t: steps.append(k),
        max_tokens_message="Raise the response length in Settings.",
    )
    assert outcome.cut_short == "Raise the response length in Settings."
    assert [r.content for r in conversation.answered[0]] == ["ok"]
    assert outcome.cost is None  # replies that carry no cost make the total unknown, not zero


def test_a_refusal_ends_the_turn_and_says_so(suite, vendors):
    vendors.respond = lambda r: httpx.Response(200, json={
        "model": "claude-opus-5-5", "content": [], "stop_reason": "refusal",
        "stop_details": {"category": "cyber"}, "usage": {}})
    outcome = run_agent(SuiteChat(suite).conversation(system=[], prompt="x"))
    assert outcome.cut_short == "The model declined this request (cyber)."


def test_streaming_hands_over_text_as_it_arrives(suite, vendors):
    events = [
        {"type": "message_start", "message": {"model": "claude-opus-5-5", "content": [], "usage": {}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hi "}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "there"}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
        {"type": "message_stop"},
    ]
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()
    vendors.respond = lambda r: httpx.Response(200, content=body)
    chunks = []
    reply = SuiteChat(suite).conversation(system=[], prompt="x").ask(on_delta=chunks.append)
    assert chunks == ["Hi ", "there"] and reply.text == "Hi there"


def test_resolve_tells_an_app_whether_to_offer_ai(suite):
    chat = SuiteChat(suite, task="chat.assistant")
    assert chat.model == "claude-opus-5-5" and chat.supports_images
    assert SuiteChat(suite, task="video.generate").resolve() is None


def test_nothing_routed_is_not_configured_not_a_crash(auth_client):
    client = SuiteClient(token="t", http=auth_client)
    with pytest.raises(SuiteNotConfigured):
        SuiteChat(client).conversation(system=[], prompt="x").ask()


def test_an_unreachable_suite_is_unavailable():
    def refuse(request):
        raise httpx.ConnectError("refused")

    client = SuiteClient("http://suite.invalid", token="t", transport=httpx.MockTransport(refuse))
    with pytest.raises(SuiteUnavailable):
        SuiteChat(client).conversation(system=[], prompt="x").ask()


def test_embeddings_and_search_through_the_package(auth_client, catalog, vendors):
    pid = auth_client.post("/api/v1/providers", json={"name": "OpenAI", "vendor": "openai", "api_key": "k"}).json()["data"]["id"]
    mid = auth_client.post(f"/api/v1/providers/{pid}/models", json={"model_id": "text-embedding-3-small"}).json()["data"]["id"]
    auth_client.put("/api/v1/assignments", json={"task": "embed.text", "model_ref": mid})
    client = SuiteClient(token="t", http=auth_client)

    vendors.respond = lambda r: httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]})
    embedder = SuiteEmbedder(client, app="gam", dimensions=2)
    assert embedder.embed(["owl"]) == [[0.1, 0.2]]
    assert (embedder.model, embedder.dimensions) == ("text-embedding-3-small", 2)

    vendors.respond = lambda r: httpx.Response(200, text='<div class="result"><a class="result__a" href="https://o.example">Owl</a></div>')
    assert web_search(client, "owls")[0]["url"] == "https://o.example"


def test_an_embedder_knows_its_model_before_it_embeds_anything(auth_client, catalog, vendors):
    """GAM reads `model` to pick the vectors a query may be compared with, and to report
    coverage: neither of which embeds."""
    client = SuiteClient(token="t", http=auth_client)
    assert SuiteEmbedder(client, app="gam").resolve() is None  # nothing routed yet
    assert SuiteEmbedder(client, app="gam").model == ""

    pid = auth_client.post("/api/v1/providers", json={"name": "OpenAI", "vendor": "openai", "api_key": "k"}).json()["data"]["id"]
    mid = auth_client.post(f"/api/v1/providers/{pid}/models", json={"model_id": "text-embedding-3-small"}).json()["data"]["id"]
    auth_client.put("/api/v1/assignments", json={"task": "embed.text", "model_ref": mid})

    vendors.respond = lambda r: pytest.fail("resolving must not call a vendor")
    embedder = SuiteEmbedder(client, app="gam")
    assert embedder.model == "text-embedding-3-small"
    assert embedder.dimensions == 1536  # the model's own size...
    assert SuiteEmbedder(client, app="gam", dimensions=512).dimensions == 512  # ...unless asked for fewer
    assert vendors.calls == []


def test_an_app_credential_through_the_package(client, auth_client, switch_user, admin, catalog, vendors):
    from app.auth import current_user
    from app.main import app as fastapi_app

    switch_user(ADMIN_ID)
    secret = auth_client.post("/api/v1/apps/gvc/secret").json()["data"]["secret"]
    switch_user(TEST_USER_ID)
    _setup_claude(auth_client)
    fastapi_app.dependency_overrides.pop(current_user, None)

    vendors.respond = lambda r: _reply([{"type": "text", "text": "ok"}])
    suite = SuiteClient(app="gvc", app_secret=secret, on_behalf_of=TEST_USER_ID, http=client)
    assert SuiteChat(suite).conversation(system=[], prompt="x").ask().text == "ok"
    with pytest.raises(SuiteError) as excinfo:
        suite.request("POST", "/api/v1/providers", json={"name": "x", "vendor": "openai"})
    assert excinfo.value.code == "app_forbidden"


def test_a_client_needs_exactly_one_way_in():
    with pytest.raises(ValueError):
        SuiteClient("http://x")
    with pytest.raises(ValueError):
        SuiteClient("http://x", app="gam", app_secret="s")
