"""Accounts in the suite (M6), through the real routes.

The tests that matter most are the cutover's: an imported Notes account signs in here with
the password and authenticator it already had; a session the suite issues is the same
token Notes issues, so every app accepts it; and a token Notes issued still works here.
The rest is Notes' flows, ported, behaving as they did.
"""

import base64
import hashlib
import json
import sqlite3
from datetime import datetime

import pyotp
import pytest
from cryptography.fernet import Fernet
from jose import jwt
from sqlmodel import select

from app.config import settings
from app.limiter import limiter
from app.models.user import AuthToken, User

AUTH = "/api/v1/auth"
ORIGIN = {"Origin": "http://testserver"}

# A real gecko-notes hash of "notes-password-123", made by Notes' own passlib code, and the
# secret its users' authenticators hold. These are what an imported account carries.
NOTES_HASH = "$2b$12$RCD4c8dsy7jpz3pAwVGcveY1VpyOkxLytUzgmP/v0KePlLCL5pnVu"
NOTES_PASSWORD = "notes-password-123"
TOTP_SECRET = "JBSWY3DPEHPK3PXP"


@pytest.fixture(autouse=True)
def fresh_limits():
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def mailbox(monkeypatch):
    """Email switched on, and every message caught instead of sent."""
    sent = []
    monkeypatch.setattr(settings, "smtp_host", "smtp.test")
    monkeypatch.setattr(settings, "smtp_from", "suite@test")
    from app.accounts import mail
    monkeypatch.setattr(mail, "send", lambda to, template, subject, context, background=None:
                        sent.append({"to": to, "template": template, **context}))
    return sent


def register(client, username="alice", email="alice@example.com", password="correct-horse"):
    return client.post(f"{AUTH}/register", json={"username": username, "email": email, "password": password})


def sign_in(client, username="alice", password="correct-horse"):
    return client.post(f"{AUTH}/login", json={"username": username, "password": password})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


# ─── the token is the token every app already accepts ────────────────────────


def test_the_first_account_is_the_administrator_and_signs_in_with_notes_token(client):
    res = register(client)
    assert res.status_code == 201 and res.json()["data"]["is_admin"] is True

    res = sign_in(client)
    assert res.status_code == 200
    data = res.json()["data"]
    claims = jwt.decode(data["access_token"], settings.jwt_secret_key, algorithms=["HS256"])
    # Notes' claims, plus email and is_admin for apps that meet someone for the first time.
    assert claims["sub"] == data["user"]["id"] and claims["username"] == "alice"
    assert claims["email"] == "alice@example.com" and claims["is_admin"] is True

    cookie = res.headers["set-cookie"]
    assert "gecko_session=" in cookie and "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    assert f"Max-Age={30 * 24 * 3600}" in cookie

    # Both transports every app reads: the header, and the shared cookie.
    assert client.get(f"{AUTH}/me", headers=bearer(data["access_token"])).json()["data"]["username"] == "alice"
    client.cookies.set("gecko_session", data["access_token"])
    assert client.get(f"{AUTH}/me").status_code == 200


def test_a_token_notes_issued_still_works_here(client, session):
    register(client)
    user = session.exec(select(User).where(User.username == "alice")).one()
    notes_token = jwt.encode({"sub": user.id, "username": "alice", "exp": 4102444800},
                             settings.jwt_secret_key, algorithm="HS256")
    res = client.get(f"{AUTH}/session", headers=bearer(notes_token))
    assert res.status_code == 200 and res.json()["data"]["user"]["id"] == user.id


def test_the_second_account_is_not_an_administrator_and_sign_up_can_be_closed(client, session):
    register(client)
    assert register(client, "bob", "bob@example.com").json()["data"]["is_admin"] is False
    from app.accounts import instance
    instance.put(session, instance.REGISTRATION_ENABLED, False)
    session.commit()
    res = register(client, "carol", "carol@example.com")
    assert res.status_code == 403 and res.json()["detail"]["code"] == "registration_closed"


def test_names_and_addresses_are_taken_once(client):
    register(client)
    assert register(client, "alice", "other@example.com").json()["detail"]["code"] == "username_taken"
    assert register(client, "other", "ALICE@example.com").json()["detail"]["code"] == "email_taken"


def test_a_short_password_is_refused(client):
    assert register(client, password="short").status_code == 422


def test_a_wrong_password_and_a_disabled_account(client, session):
    register(client)
    assert sign_in(client, password="wrong-one!").json()["detail"]["code"] == "invalid_credentials"
    token = sign_in(client).json()["data"]["access_token"]

    user = session.exec(select(User).where(User.username == "alice")).one()
    user.is_active = False
    session.add(user)
    session.commit()
    assert sign_in(client).json()["detail"]["code"] == "account_disabled"
    # A token issued before the account was disabled stops working here too.
    res = client.get(f"{AUTH}/me", headers=bearer(token))
    assert res.status_code == 403 and res.json()["detail"]["code"] == "account_disabled"


def test_signing_in_is_rate_limited(client):
    register(client)
    codes = [sign_in(client, password="wrong-one!").status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429


def test_signing_out_clears_the_shared_cookie(client):
    res = client.post(f"{AUTH}/logout")
    assert res.status_code == 204 and 'gecko_session=""' in res.headers["set-cookie"]


# ─── two-factor ──────────────────────────────────────────────────────────────


def test_an_authenticator_is_set_up_then_asked_for_at_sign_in(client):
    register(client)
    token = sign_in(client).json()["data"]["access_token"]
    setup = client.post(f"{AUTH}/2fa/totp/setup", headers=bearer(token)).json()["data"]
    assert setup["qr_data_uri"].startswith("data:image/svg+xml;base64,")
    code = pyotp.TOTP(setup["secret"]).now()
    status = client.post(f"{AUTH}/2fa/totp/enable", headers=bearer(token),
                         json={"secret": setup["secret"], "code": code}).json()["data"]
    assert status == {"enabled": True, "method": "totp", "email_available": False}

    first = sign_in(client).json()["data"]
    assert first["two_factor_required"] is True and "access_token" not in first
    # The challenge is not a session, and a session is not a challenge.
    assert client.get(f"{AUTH}/me", headers=bearer(first["challenge_token"])).status_code in (401, 404)
    assert client.post(f"{AUTH}/login/2fa", json={"challenge_token": token, "code": code}).status_code == 401

    bad = client.post(f"{AUTH}/login/2fa", json={"challenge_token": first["challenge_token"], "code": "000000"})
    assert bad.json()["detail"]["code"] == "code_invalid"
    good = client.post(f"{AUTH}/login/2fa", json={"challenge_token": first["challenge_token"],
                                                  "code": pyotp.TOTP(setup["secret"]).now()})
    assert good.status_code == 200 and "access_token" in good.json()["data"]


def test_an_emailed_code_is_capped_at_five_wrong_tries(client, mailbox):
    register(client)
    token = sign_in(client).json()["data"]["access_token"]
    client.post(f"{AUTH}/2fa/email/setup", headers=bearer(token))
    client.post(f"{AUTH}/2fa/email/verify", headers=bearer(token), json={"code": mailbox[-1]["code"]})

    challenge = sign_in(client).json()["data"]["challenge_token"]
    code = mailbox[-1]["code"]
    for _ in range(5):
        client.post(f"{AUTH}/login/2fa", json={"challenge_token": challenge, "code": "999999" if code != "999999" else "111111"})
    late = client.post(f"{AUTH}/login/2fa", json={"challenge_token": challenge, "code": code})
    assert late.status_code in (401, 429)  # spent after five wrong tries, even with the right code


def test_turning_two_factor_off_needs_the_password(client):
    register(client)
    token = sign_in(client).json()["data"]["access_token"]
    assert client.post(f"{AUTH}/2fa/disable", headers=bearer(token), json={"password": "nope-nope"}).status_code == 400
    assert client.post(f"{AUTH}/2fa/disable", headers=bearer(token),
                       json={"password": "correct-horse"}).json()["data"]["enabled"] is False


# ─── verification and recovery ───────────────────────────────────────────────


def test_a_new_address_is_verified_before_signing_in(client, mailbox):
    register(client)  # the first account is never held back
    register(client, "bob", "bob@example.com")
    assert sign_in(client, "bob").json()["detail"]["code"] == "email_not_verified"
    link = mailbox[-1]["verify_url"]
    assert link.startswith(settings.app_base_url)
    assert client.post(f"{AUTH}/verify-email", json={"token": link.split("token=")[1]}).status_code == 200
    assert sign_in(client, "bob").status_code == 200


def test_a_reset_link_works_once(client, mailbox):
    register(client)
    client.post(f"{AUTH}/forgot-password", json={"email": "alice@example.com"})
    raw = mailbox[-1]["reset_url"].split("token=")[1]
    assert client.post(f"{AUTH}/reset-password", json={"token": raw, "new_password": "brand-new-pass"}).status_code == 200
    assert sign_in(client, password="correct-horse").status_code == 401
    assert sign_in(client, password="brand-new-pass").status_code == 200
    assert client.post(f"{AUTH}/reset-password", json={"token": raw, "new_password": "again-again"}).status_code == 400


def test_recovery_says_the_same_whether_or_not_the_address_exists(client, mailbox):
    register(client)
    known = client.post(f"{AUTH}/forgot-password", json={"email": "alice@example.com"}).json()
    unknown = client.post(f"{AUTH}/forgot-password", json={"email": "nobody@example.com"}).json()
    assert known == unknown and len(mailbox) == 1


# ─── administration ──────────────────────────────────────────────────────────


def test_administration_is_for_administrators_and_keeps_one(client, session):
    admin = sign_in(client) if register(client) else None
    admin_token = admin.json()["data"]["access_token"]
    register(client, "bob", "bob@example.com")
    bob_token = sign_in(client, "bob").json()["data"]["access_token"]
    session.add(User(id="shadow-1", username="ghost"))  # seen only through a token: not an account
    session.commit()

    assert client.get("/api/v1/admin/users", headers=bearer(bob_token)).status_code == 403
    listed = client.get("/api/v1/admin/users", headers=bearer(admin_token)).json()
    assert {u["username"] for u in listed["data"]} == {"alice", "bob"} and listed["total"] == 2

    alice_id = sign_in(client).json()["data"]["user"]["id"]
    res = client.patch(f"/api/v1/admin/users/{alice_id}", headers=bearer(admin_token), json={"is_admin": False})
    assert res.json()["detail"]["code"] == "last_admin"
    assert client.delete(f"/api/v1/admin/users/{alice_id}", headers=bearer(admin_token)).json()["detail"]["code"] == "own_account"

    settings_ = client.put("/api/v1/admin/settings", headers=bearer(admin_token),
                           json={"registration_enabled": False}).json()["data"]
    assert settings_ == {"registration_enabled": False, "email_verification_required": True}


def test_deleting_an_account_leaves_what_they_made(client, session):
    register(client)
    admin_token = sign_in(client).json()["data"]["access_token"]
    bob_id = register(client, "bob", "bob@example.com").json()["data"]["id"]
    from app.models.ai import AIProvider
    session.add(AIProvider(id="p-bob", scope="user", user_id=bob_id, name="Bob's", vendor="openai"))
    session.commit()
    assert client.delete(f"/api/v1/admin/users/{bob_id}", headers=bearer(admin_token)).status_code == 204
    session.expire_all()
    assert session.get(User, bob_id) is None and session.get(AIProvider, "p-bob") is not None


# ─── the importer: Notes accounts sign in here unchanged ─────────────────────


def _notes_db(tmp_path, *, totp_secret_stored):
    path = tmp_path / "notes.db"
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE user (id TEXT PRIMARY KEY, username TEXT, email TEXT, hashed_password TEXT,
                  is_active BOOLEAN, is_admin BOOLEAN, avatar_url TEXT, created_at TEXT, last_login TEXT,
                  email_verified BOOLEAN, two_factor_method TEXT, totp_secret TEXT)""")
    db.execute("INSERT INTO user VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
               ("notes-user-1", "davior", "d@example.com", NOTES_HASH, 1, 1, None,
                "2026-01-02T03:04:05", None, 1, "totp", totp_secret_stored))
    db.execute("CREATE TABLE appsetting (key TEXT PRIMARY KEY, value TEXT)")
    db.execute("INSERT INTO appsetting VALUES ('registration_enabled', 'false')")
    db.commit()
    db.close()
    return path


def _notes_encrypt(secret: str) -> str:
    """How Notes stored it: Fernet under a key derived from the shared JWT secret."""
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.jwt_secret_key.encode()).digest())
    return "enc:" + Fernet(key).encrypt(secret.encode()).decode()


def test_an_imported_notes_account_signs_in_with_its_password_and_authenticator(client, session, tmp_path, catalog):
    from app.accounts import instance
    from app.tools.import_legacy import run

    report = run(session, {"notes": _notes_db(tmp_path, totp_secret_stored=_notes_encrypt(TOTP_SECRET))},
                 settings.jwt_secret_key)
    assert report.accounts == 1 and not report.warnings

    user = session.get(User, "notes-user-1")
    assert user.is_admin and user.email_verified and user.two_factor_method == "totp"
    from app import crypto
    assert crypto.decrypt(user.totp_secret) == TOTP_SECRET  # re-encrypted under the suite's own keys
    assert instance.get(session, instance.REGISTRATION_ENABLED) is False

    first = sign_in(client, "davior", NOTES_PASSWORD).json()["data"]
    done = client.post(f"{AUTH}/login/2fa", json={"challenge_token": first["challenge_token"],
                                                  "code": pyotp.TOTP(TOTP_SECRET).now()})
    assert done.status_code == 200
    assert jwt.decode(done.json()["data"]["access_token"], settings.jwt_secret_key,
                      algorithms=["HS256"])["sub"] == "notes-user-1"  # the id every app already knows

    # Again: nothing changes, and a password set here since is never overwritten.
    user.hashed_password = "$2b$12$changed-here"
    session.add(user)
    session.commit()
    again = run(session, {"notes": tmp_path / "notes.db"}, settings.jwt_secret_key)
    assert again.accounts == 0 and session.get(User, "notes-user-1").hashed_password == "$2b$12$changed-here"


def test_an_unreadable_authenticator_comes_across_off_and_said(client, session, tmp_path, catalog):
    from app.tools.import_legacy import run

    other_key = base64.urlsafe_b64encode(hashlib.sha256(b"an older secret").digest())
    stored = "enc:" + Fernet(other_key).encrypt(TOTP_SECRET.encode()).decode()
    report = run(session, {"notes": _notes_db(tmp_path, totp_secret_stored=stored)}, settings.jwt_secret_key)
    assert report.accounts == 1 and any("two-factor turned off" in w for w in report.warnings)
    assert session.get(User, "notes-user-1").two_factor_method is None
    # Not locked out: the password alone signs in, and they can set the authenticator up again.
    assert "access_token" in sign_in(client, "davior", NOTES_PASSWORD).json()["data"]
