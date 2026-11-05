"""Providers: scope, isolation, the key never leaving, SSRF, and the connection test."""

import httpx

from tests.conftest import ADMIN_ID, TEST_USER_ID


def _create(client, **fields):
    body = {"name": "Mine", "vendor": "anthropic", "api_key": "sk-ant-secret", **fields}
    return client.post("/api/v1/providers", json=body)


def test_the_key_goes_out_as_a_boolean_and_is_stored_encrypted(auth_client, session):
    from app.models.ai import AIProvider

    res = _create(auth_client)
    assert res.status_code == 201, res.text
    data = res.json()["data"]
    assert data["api_key_configured"] is True and data["writable"] is True
    assert data["scope"] == "user"
    assert "sk-ant-secret" not in res.text

    row = session.get(AIProvider, data["id"])
    assert row.api_key.startswith("enc:") and "sk-ant-secret" not in row.api_key
    assert row.user_id == TEST_USER_ID


def test_a_user_never_sees_another_users_provider(auth_client, switch_user):
    mine = _create(auth_client).json()["data"]["id"]

    switch_user("someone-else")
    assert auth_client.get("/api/v1/providers").json()["data"] == []
    # 404, not 403: whether the id exists is not theirs to learn.
    assert auth_client.patch(f"/api/v1/providers/{mine}", json={"name": "x"}).status_code == 404
    assert auth_client.delete(f"/api/v1/providers/{mine}").status_code == 404
    assert auth_client.get(f"/api/v1/providers/{mine}/models").status_code == 404


def test_only_an_admin_creates_or_changes_a_shared_provider(auth_client, switch_user, admin):
    assert _create(auth_client, scope="instance").status_code == 403

    switch_user(ADMIN_ID)
    shared = _create(auth_client, scope="instance", name="Shared Claude")
    assert shared.status_code == 201
    shared_id = shared.json()["data"]["id"]

    switch_user(TEST_USER_ID)
    listed = auth_client.get("/api/v1/providers").json()["data"]
    assert [(p["name"], p["writable"]) for p in listed] == [("Shared Claude", False)]
    res = auth_client.patch(f"/api/v1/providers/{shared_id}", json={"name": "Hijacked"})
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "admin_required"


def test_custom_base_urls_are_ssrf_checked(auth_client):
    res = _create(auth_client, vendor="custom", base_url="https://127.0.0.1/v1")
    assert res.status_code == 400 and res.json()["detail"]["code"] == "ssrf_blocked"
    res = _create(auth_client, vendor="openai", base_url="http://api.example.com")
    assert res.json()["detail"]["code"] == "invalid_url"
    # Ollama is the deliberate exception: a local daemon.
    assert _create(auth_client, vendor="ollama", api_key="", base_url="http://localhost:11434").status_code == 201


def test_the_anthropic_switch_is_refused_where_it_would_do_nothing(auth_client):
    res = _create(auth_client, vendor="openai", use_anthropic_api=True)
    assert res.status_code == 400
    assert _create(auth_client, vendor="deepseek", use_anthropic_api=True).status_code == 201


def test_unknown_vendor_is_a_400(auth_client):
    assert _create(auth_client, vendor="skynet").json()["detail"]["code"] == "bad_vendor"


def test_the_key_is_three_state_on_update(auth_client, session):
    from app import crypto
    from app.models.ai import AIProvider

    pid = _create(auth_client).json()["data"]["id"]
    auth_client.patch(f"/api/v1/providers/{pid}", json={"name": "Renamed"})
    session.expire_all()
    assert crypto.decrypt(session.get(AIProvider, pid).api_key) == "sk-ant-secret"

    res = auth_client.patch(f"/api/v1/providers/{pid}", json={"api_key": ""})
    assert res.json()["data"]["api_key_configured"] is False


def test_deleting_a_provider_takes_its_models_and_routing(auth_client, session):
    from app.models.ai import AIModel, TaskAssignment

    pid = _create(auth_client).json()["data"]["id"]
    mid = auth_client.post(
        f"/api/v1/providers/{pid}/models",
        json={"model_id": "claude-x", "capabilities": ["chat"]},
    ).json()["data"]["id"]
    auth_client.put("/api/v1/assignments", json={"task": "chat.assistant", "model_ref": mid})

    assert auth_client.delete(f"/api/v1/providers/{pid}").status_code == 204
    session.expire_all()
    assert session.get(AIModel, mid) is None
    assert session.exec(__import__("sqlmodel").select(TaskAssignment)).all() == []


# ─── connection test ─────────────────────────────────────────────────────────


def test_a_saved_provider_is_tested_with_its_stored_key(auth_client, vendors):
    pid = _create(auth_client).json()["data"]["id"]
    vendors.respond = lambda request: httpx.Response(200, json={})

    res = auth_client.post(
        "/api/v1/providers/test", json={"provider_id": pid, "model_id": "claude-opus-5-5"}
    )
    assert res.json()["data"] == {"success": True, "message": "Connected"}
    sent = vendors.calls[-1]
    assert str(sent.url) == "https://api.anthropic.com/v1/messages"
    assert sent.headers["x-api-key"] == "sk-ant-secret"
    assert "sk-ant-secret" not in res.text


def test_deepseek_on_the_anthropic_protocol_is_tested_at_that_endpoint(auth_client, vendors):
    res = auth_client.post(
        "/api/v1/providers/test",
        json={"vendor": "deepseek", "api_key": "k", "use_anthropic_api": True, "model_id": "deepseek-chat"},
    )
    assert res.json()["data"]["success"] is True
    assert str(vendors.calls[-1].url) == "https://api.deepseek.com/anthropic/v1/messages"


def test_a_rejected_key_says_so(auth_client, vendors):
    vendors.respond = lambda request: httpx.Response(401, json={})
    res = auth_client.post(
        "/api/v1/providers/test", json={"vendor": "openai", "api_key": "bad", "model_id": "gpt-4.1"}
    )
    assert res.json()["data"] == {"success": False, "message": "The API key was rejected"}
    assert str(vendors.calls[-1].url) == "https://api.openai.com/v1/chat/completions"


def test_another_users_provider_cannot_be_probed(auth_client, switch_user, vendors):
    pid = _create(auth_client).json()["data"]["id"]
    switch_user("someone-else")
    res = auth_client.post("/api/v1/providers/test", json={"provider_id": pid, "model_id": "m"})
    assert res.status_code == 404
    assert vendors.calls == []


# ─── vendors that are not chat models ─────────────────────────────────────────


def test_a_deepgram_key_is_tested_by_asking_for_its_projects(auth_client, vendors):
    res = auth_client.post("/api/v1/providers", json={"name": "Deepgram", "vendor": "deepgram",
                                                     "api_key": "dg-secret"})
    pid = res.json()["data"]["id"]
    # No model is needed: the key is the whole question.
    res = auth_client.post("/api/v1/providers/test", json={"provider_id": pid})
    assert res.json()["data"] == {"success": True, "message": "Connected"}
    sent = vendors.calls[-1]
    assert (sent.method, str(sent.url)) == ("GET", "https://api.deepgram.com/v1/projects")
    assert sent.headers["authorization"] == "Token dg-secret"
    assert "dg-secret" not in res.text


def test_a_fal_key_is_tested_with_a_price_lookup_that_spends_nothing(auth_client, vendors):
    res = auth_client.post("/api/v1/providers/test", json={"vendor": "fal", "api_key": "fal-secret"})
    assert res.json()["data"]["success"] is True
    sent = vendors.calls[-1]
    assert sent.method == "GET" and sent.url.path == "/v1/models/pricing"
    assert sent.headers["authorization"] == "Key fal-secret"


def test_a_rejected_speech_key_says_so(auth_client, vendors):
    vendors.respond = lambda request: httpx.Response(401, json={})
    res = auth_client.post("/api/v1/providers/test", json={"vendor": "deepgram", "api_key": "bad"})
    assert res.json()["data"] == {"success": False, "message": "The API key was rejected"}
