def test_health_is_public(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_config_is_public_and_carries_only_the_sign_in_address(client):
    res = client.get("/api/config")
    assert res.status_code == 200
    # Readable by anyone, so adding a field has to be a deliberate act.
    assert set(res.json()["data"]) == {"notes_base_url"}
