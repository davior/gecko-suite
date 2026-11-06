def test_health_is_public(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_config_is_public_and_carries_only_what_sign_in_needs(client):
    res = client.get("/api/config")
    assert res.status_code == 200
    # Readable by anyone, so adding a field has to be a deliberate act. M6 added the two
    # the sign-in page needs: where it may send someone back to, and whether email works.
    assert set(res.json()["data"]) == {"login_redirect_origins", "email_enabled"}
