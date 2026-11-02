"""Models, the catalogue and its prices, and which model a task resolves to."""

import pytest

from tests.conftest import ADMIN_ID, TEST_USER_ID


def _provider(client, **fields):
    body = {"name": "P", "vendor": "anthropic", "api_key": "k", **fields}
    res = client.post("/api/v1/providers", json=body)
    assert res.status_code == 201, res.text
    return res.json()["data"]["id"]


def _model(client, provider_id, **fields):
    res = client.post(f"/api/v1/providers/{provider_id}/models", json=fields)
    assert res.status_code == 201, res.text
    return res.json()["data"]


def _route(client, task, model_ref, **fields):
    return client.put("/api/v1/assignments", json={"task": task, "model_ref": model_ref, **fields})


# ─── catalogue and pricing ───────────────────────────────────────────────────


def test_a_known_model_is_filled_in_from_the_catalogue(auth_client, catalog):
    model = _model(auth_client, _provider(auth_client), model_id="claude-opus-5-5")
    assert model["label"] == "Claude Opus 5.5"
    assert {"chat", "vision", "tools"} <= set(model["capabilities"])
    assert model["max_tokens"] == 128_000
    # Not copied: the catalogue's price applies, so a later price migration reaches it.
    assert model["pricing"] is None
    assert model["effective_pricing"] == {
        "currency": "USD", "input": 4.0, "output": 20.0, "cache_read": 0.20
    }


def test_an_unknown_id_is_priced_by_its_family_and_described_by_nobody(auth_client, catalog):
    model = _model(auth_client, _provider(auth_client), model_id="claude-sonnet-9")
    assert model["capabilities"] == []  # a family row is a price, not a description
    assert model["effective_pricing"]["input"] == 2.0


def test_the_longest_family_wins(auth_client, catalog):
    model = _model(auth_client, _provider(auth_client), model_id="claude-opus-4-1-20250805")
    assert model["effective_pricing"]["input"] == 15.0


def test_ollama_is_free_and_custom_is_unknown(auth_client, catalog):
    local = _model(auth_client, _provider(auth_client, vendor="ollama", api_key=""), model_id="llama3")
    assert local["effective_pricing"] == {"currency": "USD", "input": 0.0, "output": 0.0}
    gateway = _model(
        auth_client,
        _provider(auth_client, vendor="custom", base_url="https://gateway.example.com"),
        model_id="claude-opus-5-5",
    )
    # Unknown is None, never 0: a custom endpoint's price is not the vendor's.
    assert gateway["effective_pricing"] is None


def test_a_models_own_price_wins_and_clearing_it_restores_the_catalogues(auth_client, catalog):
    model = _model(
        auth_client, _provider(auth_client), model_id="claude-opus-5-5",
        pricing={"currency": "USD", "input": 1.0, "output": 2.0},
    )
    assert model["effective_pricing"]["input"] == 1.0
    res = auth_client.patch(f"/api/v1/models/{model['id']}", json={"pricing": {}})
    assert res.json()["data"]["effective_pricing"]["input"] == 4.0


def test_the_catalogue_lists_models_not_families(auth_client, catalog):
    ids = [e["model_id"] for e in auth_client.get("/api/v1/catalog?vendor=anthropic").json()["data"]]
    assert "claude-opus-5-5" in ids and not any(i.endswith("*") for i in ids)


def test_unknown_capabilities_are_refused(auth_client):
    res = auth_client.post(
        f"/api/v1/providers/{_provider(auth_client)}/models",
        json={"model_id": "m", "capabilities": ["telepathy"]},
    )
    assert res.json()["detail"]["code"] == "bad_capability"


def test_models_can_be_filtered_by_capability(auth_client, catalog):
    pid = _provider(auth_client)
    _model(auth_client, pid, model_id="claude-opus-5-5")
    _model(auth_client, _provider(auth_client, vendor="ollama", api_key=""), model_id="nomic-embed-text")
    embedders = auth_client.get("/api/v1/models?capability=embed").json()["data"]
    assert [m["model_id"] for m in embedders] == ["nomic-embed-text"]


# ─── assignments ─────────────────────────────────────────────────────────────


def test_a_task_needs_a_model_that_can_do_it(auth_client, catalog):
    deepseek = _model(auth_client, _provider(auth_client, vendor="deepseek"), model_id="deepseek-chat")
    res = _route(auth_client, "enrich.describe", deepseek["id"])
    assert res.status_code == 400 and res.json()["detail"]["code"] == "missing_capability"
    assert _route(auth_client, "chat.assistant", deepseek["id"]).status_code == 200


def test_shared_routing_cannot_point_at_a_personal_key(auth_client, switch_user, admin, catalog):
    switch_user(ADMIN_ID)
    personal = _model(auth_client, _provider(auth_client), model_id="claude-opus-5-5")
    res = _route(auth_client, "chat.assistant", personal["id"], scope="instance")
    assert res.status_code == 400


def test_only_an_admin_sets_shared_routing(auth_client, switch_user, admin, catalog):
    switch_user(ADMIN_ID)
    shared = _model(auth_client, _provider(auth_client, scope="instance"), model_id="claude-opus-5-5")
    switch_user(TEST_USER_ID)
    assert _route(auth_client, "chat.assistant", shared["id"], scope="instance").status_code == 403
    # But anyone may route their own tasks to a shared model.
    assert _route(auth_client, "chat.assistant", shared["id"]).status_code == 200


def test_put_replaces_rather_than_duplicates(auth_client, catalog):
    pid = _provider(auth_client)
    a = _model(auth_client, pid, model_id="claude-opus-5-5")
    b = _model(auth_client, pid, model_id="claude-haiku-5-5")
    _route(auth_client, "chat.quick", a["id"])
    _route(auth_client, "chat.quick", b["id"])
    rows = auth_client.get("/api/v1/assignments").json()["data"]
    assert [(r["task"], r["model_id"]) for r in rows] == [("chat.quick", "claude-haiku-5-5")]


# ─── resolution ──────────────────────────────────────────────────────────────


@pytest.fixture(name="ladder")
def ladder_fixture(auth_client, switch_user, admin, catalog):
    """Four models, one per resolution rule, all routed for chat.assistant."""
    switch_user(ADMIN_ID)
    shared = _provider(auth_client, scope="instance", name="Shared")
    instance = _model(auth_client, shared, model_id="claude-opus-5-5")
    instance_gam = _model(auth_client, shared, model_id="claude-sonnet-5-5")
    _route(auth_client, "chat.assistant", instance["id"], scope="instance")
    _route(auth_client, "chat.assistant", instance_gam["id"], scope="instance", app="gam")

    switch_user(TEST_USER_ID)
    own = _provider(auth_client, name="Mine")
    user = _model(auth_client, own, model_id="claude-haiku-5-5")
    user_gam = _model(auth_client, own, model_id="claude-fable-5-1")
    return {"instance": instance, "instance_gam": instance_gam, "user": user, "user_gam": user_gam}


def _resolve(client, task="chat.assistant", app=None):
    url = f"/api/v1/resolve?task={task}" + (f"&app={app}" if app else "")
    return client.get(url).json()["data"]


def test_resolution_order_is_user_app_then_user_then_instance_app_then_instance(auth_client, ladder):
    assert _resolve(auth_client, app="gam")["rule"] == "instance+app"
    assert _resolve(auth_client, app="notes")["rule"] == "instance"

    _route(auth_client, "chat.assistant", ladder["user"]["id"])
    assert _resolve(auth_client, app="gam")["model"]["model_id"] == "claude-haiku-5-5"

    _route(auth_client, "chat.assistant", ladder["user_gam"]["id"], app="gam")
    hit = _resolve(auth_client, app="gam")
    assert (hit["rule"], hit["model"]["model_id"]) == ("user+app", "claude-fable-5-1")
    assert _resolve(auth_client, app="notes")["rule"] == "user"


def test_a_finer_task_falls_back_to_its_parent(auth_client, ladder):
    hit = _resolve(auth_client, task="chat.draft")
    assert (hit["matched_task"], hit["model"]["model_id"]) == ("chat.assistant", "claude-opus-5-5")


def test_an_explicit_shared_task_beats_a_users_fallback(auth_client, switch_user, ladder):
    """A user's chat.assistant must not override the admin's explicit chat.draft."""
    switch_user(ADMIN_ID)
    _route(auth_client, "chat.draft", ladder["instance_gam"]["id"], scope="instance")
    switch_user(TEST_USER_ID)
    _route(auth_client, "chat.assistant", ladder["user"]["id"])
    assert _resolve(auth_client, task="chat.draft")["model"]["model_id"] == "claude-sonnet-5-5"


def test_a_switched_off_provider_falls_through_to_the_next_rule(auth_client, ladder):
    _route(auth_client, "chat.assistant", ladder["user"]["id"])
    auth_client.patch(f"/api/v1/providers/{ladder['user']['provider_id']}", json={"enabled": False})
    assert _resolve(auth_client)["rule"] == "instance"


def test_nothing_routed_resolves_to_nothing(auth_client):
    data = _resolve(auth_client, task="video.generate")
    assert data["model"] is None and data["rule"] is None


def test_another_users_routing_never_applies(auth_client, switch_user, ladder):
    _route(auth_client, "chat.assistant", ladder["user"]["id"])
    switch_user("someone-else")
    assert _resolve(auth_client)["model"]["model_id"] == "claude-opus-5-5"
    assert all(r["scope"] == "instance" for r in auth_client.get("/api/v1/assignments").json()["data"])


def test_unknown_task_or_app_is_a_400(auth_client):
    assert auth_client.get("/api/v1/resolve?task=chat.gossip").status_code == 400
    assert auth_client.get("/api/v1/resolve?task=chat.assistant&app=excel").status_code == 400
