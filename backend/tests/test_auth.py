"""Real tokens through the real dependency: the auth path the suite shares with GAM and
GVC. `/api/me` is the only protected route in M0, so it stands in for all of them."""

import time

from jose import jwt

from app.config import settings


def _token(sub="notes-user-1", secret=None, exp_in=3600):
    return jwt.encode(
        {"sub": sub, "username": "gecko", "exp": int(time.time()) + exp_in},
        secret or settings.jwt_secret_key,
        algorithm="HS256",
    )


def test_no_credentials_is_401_in_the_suite_error_shape(client):
    res = client.get("/api/me")
    assert res.status_code == 401
    assert res.json()["detail"]["code"] == "unauthorized"


def test_a_token_notes_signed_is_accepted_and_records_a_shadow_user(client, session):
    from app.models import User

    res = client.get("/api/me", headers={"Authorization": f"Bearer {_token()}"})
    assert res.status_code == 200
    assert res.json()["data"]["id"] == "notes-user-1"
    assert res.json()["data"]["username"] == "gecko"
    assert res.json()["data"]["is_admin"] is False
    # The row is keyed by Notes' own id: M6 depends on that never changing.
    assert session.get(User, "notes-user-1") is not None


def test_a_token_signed_with_another_secret_is_refused(client):
    headers = {"Authorization": f"Bearer {_token(secret='some-other-secret-entirely')}"}
    assert client.get("/api/me", headers=headers).status_code == 401


def test_a_cookie_that_will_not_verify_says_the_secrets_differ(client):
    client.cookies.set("gecko_session", _token(secret="some-other-secret-entirely"))
    res = client.get("/api/me")
    assert res.status_code == 401
    assert res.json()["detail"]["code"] == "session_not_accepted"


def test_an_expired_token_is_refused(client):
    res = client.get("/api/me", headers={"Authorization": f"Bearer {_token(exp_in=-10)}"})
    assert res.status_code == 401
    assert "expired" in res.json()["detail"]["message"]


def test_admin_users_names_the_admins(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_users", "gecko")
    res = client.get("/api/me", headers={"Authorization": f"Bearer {_token()}"})
    assert res.json()["data"]["is_admin"] is True


def test_the_dev_user_needs_development_mode_too(client, monkeypatch):
    monkeypatch.setattr(settings, "dev_auth_user", "dev")
    assert client.get("/api/me").status_code == 401
    monkeypatch.setattr(settings, "environment", "development")
    assert client.get("/api/me").status_code == 200
