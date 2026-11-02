"""Embeddings, app credentials, prompts, recipes, personas and web search."""

import json

import httpx
import pytest
from sqlmodel import select

from tests.conftest import ADMIN_ID, TEST_USER_ID


def _provider(client, **fields):
    body = {"name": "P", "vendor": "anthropic", "api_key": "sk-secret", **fields}
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


def _ok_claude(request):
    return httpx.Response(200, json={"model": "claude-opus-5-5", "stop_reason": "end_turn",
                                     "content": [{"type": "text", "text": "ok"}],
                                     "usage": {"input_tokens": 1, "output_tokens": 1}})


# ─── embeddings ──────────────────────────────────────────────────────────────


def test_openai_embeddings_in_order_with_dimensions(auth_client, catalog, vendors, session):
    from app.models.usage import UsageEvent

    pid = _provider(auth_client, name="OpenAI", vendor="openai")
    _route(auth_client, "embed.text", _model(auth_client, pid, model_id="text-embedding-3-small"))
    vendors.respond = lambda r: httpx.Response(200, json={
        "data": [{"index": 1, "embedding": [0.0, 1.0]}, {"index": 0, "embedding": [1.0, 0.0]}],
        "usage": {"prompt_tokens": 7}})
    res = auth_client.post("/api/v1/ai/embed", json={"texts": ["a", "b"], "dimensions": 2, "app": "gam"})
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["vectors"] == [[1.0, 0.0], [0.0, 1.0]]
    assert (data["model"], data["dimensions"], data["input_tokens"]) == ("text-embedding-3-small", 2, 7)
    # The catalogue records no price for it: unknown, not free.
    assert data["cost"] is None
    sent = json.loads(vendors.calls[-1].content)
    assert sent == {"model": "text-embedding-3-small", "input": ["a", "b"], "dimensions": 2}
    event = session.get(UsageEvent, data["usage_event_id"])
    assert (event.kind, event.app, event.input_tokens) == ("embed", "gam", 7)


def test_ollama_embeddings_batch_and_cost_nothing(auth_client, catalog, vendors, monkeypatch):
    from app.gateway import embed

    monkeypatch.setattr(embed, "MAX_BATCH", 2)
    pid = _provider(auth_client, name="Local", vendor="ollama", api_key="", base_url="http://localhost:11434")
    _route(auth_client, "embed.text", _model(auth_client, pid, model_id="nomic-embed-text"))
    vendors.respond = lambda r: httpx.Response(200, json={
        "embeddings": [[0.5]] * len(json.loads(r.content)["input"]), "prompt_eval_count": 3})
    data = auth_client.post("/api/v1/ai/embed", json={"texts": ["a", "b", "c"]}).json()["data"]
    assert len(data["vectors"]) == 3 and len(vendors.calls) == 2
    assert str(vendors.calls[0].url) == "http://localhost:11434/api/embed"
    assert data["cost"] == 0.0 and data["input_tokens"] == 6


def test_a_chat_model_cannot_embed(auth_client, catalog):
    claude = _model(auth_client, _provider(auth_client), model_id="claude-opus-5-5")
    res = auth_client.post("/api/v1/ai/embed", json={"model_ref": claude, "texts": ["a"]})
    assert res.status_code == 400


def test_a_short_vector_count_is_an_error(auth_client, catalog, vendors):
    pid = _provider(auth_client, name="OpenAI", vendor="openai")
    _route(auth_client, "embed.text", _model(auth_client, pid, model_id="text-embedding-3-small"))
    vendors.respond = lambda r: httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})
    assert auth_client.post("/api/v1/ai/embed", json={"texts": ["a", "b"]}).status_code == 502


def test_no_embedding_model_is_a_409(auth_client):
    res = auth_client.post("/api/v1/ai/embed", json={"texts": ["a"]})
    assert res.status_code == 409 and res.json()["detail"]["code"] == "ai_not_configured"


# ─── app credentials ─────────────────────────────────────────────────────────


@pytest.fixture(name="gam_secret")
def gam_secret_fixture(auth_client, switch_user, admin, catalog, session):
    """An issued GAM credential, and a user with Claude routed for chat."""
    switch_user(ADMIN_ID)
    res = auth_client.post("/api/v1/apps/gam/secret")
    assert res.status_code == 201
    secret = res.json()["data"]["secret"]
    switch_user(TEST_USER_ID)
    _route(auth_client, "chat.assistant", _model(auth_client, _provider(auth_client), model_id="claude-opus-5-5"))
    # auth_client signs in by overriding current_user for the whole app; drop that so
    # requests made with app headers go through the real credential check.
    from app.auth import current_user
    from app.main import app as fastapi_app

    fastapi_app.dependency_overrides.pop(current_user, None)
    return secret


def _app_headers(secret, user=TEST_USER_ID, app="gam"):
    return {"X-Gecko-App": app, "X-Gecko-App-Secret": secret, "X-On-Behalf-Of": user}


def test_an_app_calls_on_a_users_behalf_and_is_credited(client, gam_secret, vendors, session):
    from app.models.library import AppClient
    from app.models.usage import UsageEvent

    vendors.respond = _ok_claude
    res = client.post("/api/v1/ai/chat", headers=_app_headers(gam_secret),
                      json={"task": "chat.assistant", "messages": [{"role": "user", "content": "Hi"}]})
    assert res.status_code == 200, res.text
    event = session.get(UsageEvent, res.json()["data"]["usage_event_id"])
    assert (event.user_id, event.app) == (TEST_USER_ID, "gam")
    row = session.get(AppClient, "gam")
    assert gam_secret not in row.secret_hash and row.last_used_at is not None


def test_an_app_cannot_claim_to_be_another(client, gam_secret, vendors):
    res = client.post("/api/v1/ai/chat", headers=_app_headers(gam_secret),
                      json={"task": "chat.assistant", "app": "notes", "messages": [{"role": "user", "content": "x"}]})
    assert res.status_code == 400 and res.json()["detail"]["code"] == "bad_app"


def test_a_wrong_secret_is_a_401_never_a_fall_through(client, gam_secret):
    res = client.get("/api/v1/models", headers=_app_headers("not-it"))
    assert res.status_code == 401 and res.json()["detail"]["code"] == "app_unauthorized"
    assert client.get("/api/v1/models", headers=_app_headers(gam_secret, app="notes")).status_code == 401


def test_an_app_credential_cannot_change_settings(client, gam_secret):
    res = client.post("/api/v1/providers", headers=_app_headers(gam_secret),
                      json={"name": "x", "vendor": "custom", "base_url": "https://evil.example.com"})
    assert res.status_code == 403 and res.json()["detail"]["code"] == "app_forbidden"
    assert client.post("/api/v1/apps/notes/secret", headers=_app_headers(gam_secret)).status_code == 403


def test_reissuing_revokes_the_old_secret(client, auth_client, gam_secret, switch_user):
    from app.auth import current_user
    from app.main import app as fastapi_app

    switch_user(ADMIN_ID)
    auth_client.post("/api/v1/apps/gam/secret")
    fastapi_app.dependency_overrides.pop(current_user, None)
    assert client.get("/api/v1/models", headers=_app_headers(gam_secret)).status_code == 401


def test_only_an_admin_issues_credentials(auth_client):
    assert auth_client.post("/api/v1/apps/gam/secret").status_code == 403
    assert auth_client.get("/api/v1/apps").status_code == 403


def test_an_app_call_needs_a_user(client, gam_secret):
    headers = _app_headers(gam_secret)
    del headers["X-On-Behalf-Of"]
    assert client.get("/api/v1/models", headers=headers).status_code == 401


# ─── prompts and recipes ─────────────────────────────────────────────────────


def test_one_default_prompt_per_owner_and_app(auth_client):
    a = auth_client.post("/api/v1/prompts", json={"name": "A", "content": "a", "is_default": True}).json()["data"]
    auth_client.post("/api/v1/prompts", json={"name": "N", "content": "n", "is_default": True, "app": "notes"})
    auth_client.post("/api/v1/prompts", json={"name": "B", "content": "b", "is_default": True})
    rows = {p["name"]: p["is_default"] for p in auth_client.get("/api/v1/prompts").json()["data"]}
    assert rows == {"A": False, "N": True, "B": True}
    assert a["writable"] is True


def test_the_app_filter_includes_the_all_apps_rows(auth_client):
    auth_client.post("/api/v1/recipes", json={"name": "Everywhere", "prompt": "p"})
    auth_client.post("/api/v1/recipes", json={"name": "GAM only", "prompt": "p", "app": "gam", "tags": ["b", "a", "a"]})
    names = [r["name"] for r in auth_client.get("/api/v1/recipes?app=notes").json()["data"]]
    assert names == ["Everywhere"]
    gam = auth_client.get("/api/v1/recipes?app=gam").json()["data"]
    assert {r["name"] for r in gam} == {"Everywhere", "GAM only"}
    assert next(r for r in gam if r["name"] == "GAM only")["tags"] == ["a", "b"]


def test_shared_prompts_are_read_only_to_users(auth_client, switch_user, admin):
    switch_user(ADMIN_ID)
    pid = auth_client.post("/api/v1/prompts", json={"scope": "instance", "name": "House", "content": "c"}).json()["data"]["id"]
    switch_user(TEST_USER_ID)
    assert [p["writable"] for p in auth_client.get("/api/v1/prompts").json()["data"]] == [False]
    assert auth_client.patch(f"/api/v1/prompts/{pid}", json={"content": "x"}).status_code == 403
    assert auth_client.post("/api/v1/prompts", json={"scope": "instance", "name": "x", "content": "x"}).status_code == 403


def test_another_users_prompt_is_invisible(auth_client, switch_user):
    pid = auth_client.post("/api/v1/prompts", json={"name": "Mine", "content": "c"}).json()["data"]["id"]
    switch_user("someone-else")
    assert auth_client.get("/api/v1/prompts").json()["data"] == []
    assert auth_client.delete(f"/api/v1/prompts/{pid}").status_code == 404


# ─── personas ────────────────────────────────────────────────────────────────


def test_a_persona_brings_its_prompt_and_its_model(auth_client, catalog, vendors):
    pid = _provider(auth_client)
    _route(auth_client, "chat.assistant", _model(auth_client, pid, model_id="claude-opus-5-5"))
    haiku = _model(auth_client, pid, model_id="claude-haiku-5-5")
    persona = auth_client.post("/api/v1/personas", json={
        "name": "Archivist", "system_prompt": "You are the archivist.",
        "model_overrides": {"chat.assistant": haiku}}).json()["data"]

    vendors.respond = _ok_claude
    auth_client.post("/api/v1/ai/chat", json={"task": "chat.assistant", "persona_id": persona["id"],
                                              "system": ["context"], "messages": [{"role": "user", "content": "Hi"}]})
    sent = json.loads(vendors.calls[-1].content)
    assert sent["model"] == "claude-haiku-5-5"
    assert [b["text"] for b in sent["system"]] == ["You are the archivist.", "context"]


def test_a_shared_persona_cannot_use_a_personal_model(auth_client, switch_user, admin, catalog):
    switch_user(ADMIN_ID)
    personal = _model(auth_client, _provider(auth_client), model_id="claude-opus-5-5")
    res = auth_client.post("/api/v1/personas", json={
        "scope": "instance", "name": "x", "model_overrides": {"chat.assistant": personal}})
    assert res.status_code == 400


def test_a_persona_model_must_fit_the_task(auth_client, catalog):
    ds = _model(auth_client, _provider(auth_client, vendor="deepseek"), model_id="deepseek-chat")
    res = auth_client.post("/api/v1/personas", json={"name": "x", "model_overrides": {"enrich.describe": ds}})
    assert res.status_code == 400


# ─── web search ──────────────────────────────────────────────────────────────

DDG_PAGE = """<html><body><div class="results">
<div class="result results_links"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fowls">Owls</a>
<a class="result__snippet">All about owls.</a></div>
<div class="result result--ad"><a class="result__a" href="https://ads.example.com">Buy owls</a></div>
</div></body></html>"""


def test_with_nothing_configured_search_uses_duckduckgo(auth_client, vendors, session):
    from app.models.usage import UsageEvent

    vendors.respond = lambda r: httpx.Response(200, text=DDG_PAGE)
    res = auth_client.post("/api/v1/search/web", json={"query": "owls", "app": "notes"})
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["kind"] == "duckduckgo"
    assert data["results"] == [{"title": "Owls", "url": "https://example.com/owls",
                                "snippet": "All about owls.", "published": None}]
    event = session.exec(select(UsageEvent)).one()
    assert (event.kind, event.app, event.cost) == ("search", "notes", 0.0)


def test_a_users_own_backend_beats_the_shared_one(auth_client, switch_user, admin, vendors, session):
    from app.models.usage import UsageEvent

    switch_user(ADMIN_ID)
    auth_client.post("/api/v1/search/providers", json={"scope": "instance", "name": "Shared", "kind": "duckduckgo"})
    switch_user(TEST_USER_ID)
    res = auth_client.post("/api/v1/search/providers", json={"name": "My Brave", "kind": "brave", "api_key": "brv"})
    assert res.json()["data"]["api_key_configured"] is True and "brv" not in res.text

    vendors.respond = lambda r: httpx.Response(200, json={"web": {"results": [
        {"title": "Owl", "url": "https://owl.example", "description": "hoot"}]}})
    data = auth_client.post("/api/v1/search/web", json={"query": "owls"}).json()["data"]
    assert (data["provider"], data["results"][0]["url"]) == ("My Brave", "https://owl.example")
    assert vendors.calls[-1].headers["x-subscription-token"] == "brv"
    # Brave bills by plan: the cost of one call is unknown, not zero.
    assert session.exec(select(UsageEvent)).one().cost is None


def test_a_searxng_url_is_ssrf_checked(auth_client):
    res = auth_client.post("/api/v1/search/providers",
                           json={"name": "x", "kind": "searxng", "base_url": "https://10.0.0.5"})
    assert res.status_code == 400


def test_a_throttled_scraper_says_so(auth_client, vendors):
    vendors.respond = lambda r: httpx.Response(200, text="<html></html>")
    res = auth_client.post("/api/v1/search/web", json={"query": "owls"})
    assert res.status_code == 502 and res.json()["detail"]["code"] == "search_blocked"


def test_an_app_credential_can_search(client, gam_secret, vendors):
    vendors.respond = lambda r: httpx.Response(200, text=DDG_PAGE)
    res = client.post("/api/v1/search/web", headers=_app_headers(gam_secret), json={"query": "owls"})
    assert res.status_code == 200
