"""Themes and which one each app wears (M7), through the real routes."""

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app as fastapi_app
from tests.conftest import ADMIN_ID, TEST_USER_ID

THEMES = "/api/v1/themes"
OCEAN = {"name": "Ocean", "mode": "dark", "bg_type": "gradient", "bg_color1": "#0a1f44", "bg_color2": "#123"}


@pytest.fixture(autouse=True)
def images_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(settings, "app_base_url", "https://suite.example.test")


def make(client, **fields):
    res = client.post(THEMES, json={**OCEAN, **fields})
    assert res.status_code == 201, res.text
    return res.json()["data"]


# ─── the library ─────────────────────────────────────────────────────────────


def test_a_person_makes_edits_and_deletes_their_own_theme(auth_client):
    theme = make(auth_client)
    assert (theme["scope"], theme["user_id"], theme["editable"]) == ("user", TEST_USER_ID, True)

    edited = auth_client.patch(f"{THEMES}/{theme['id']}", json={"glass_opacity": 0.5, "name": "Deep ocean"})
    assert edited.status_code == 200
    assert (edited.json()["data"]["glass_opacity"], edited.json()["data"]["name"]) == (0.5, "Deep ocean")
    assert edited.json()["data"]["bg_color2"] == "#123"  # untouched

    assert auth_client.delete(f"{THEMES}/{theme['id']}").status_code == 204
    assert auth_client.get(THEMES).json()["data"] == []


def test_someone_elses_theme_is_invisible(auth_client, switch_user):
    theme = make(auth_client)
    switch_user("someone-else")
    assert auth_client.get(THEMES).json()["data"] == []
    assert auth_client.patch(f"{THEMES}/{theme['id']}", json={"name": "Mine"}).status_code == 404
    assert auth_client.put(f"{THEMES}/selected/gam", json={"theme_id": theme["id"]}).status_code == 404


def test_only_an_admin_makes_or_changes_a_shared_theme(auth_client, switch_user, admin):
    assert auth_client.post(THEMES, json={**OCEAN, "scope": "instance"}).status_code == 403

    switch_user(ADMIN_ID)
    shared = make(auth_client, scope="instance", name="House")
    assert (shared["scope"], shared["user_id"], shared["editable"]) == ("instance", None, True)

    switch_user(TEST_USER_ID)
    seen = auth_client.get(THEMES).json()["data"]
    assert [t["name"] for t in seen] == ["House"] and seen[0]["editable"] is False
    assert auth_client.patch(f"{THEMES}/{shared['id']}", json={"name": "Mine"}).status_code == 403
    assert auth_client.delete(f"{THEMES}/{shared['id']}").status_code == 403


@pytest.mark.parametrize("field, value", [
    ("bg_color1", "red"),
    ("bg_color1", "#12"),
    ("bg_image_url", "javascript:alert(1)"),
    ("bg_image_url", "https://x.example/a.png); background: red"),
    ("bg_image_url", "/media/relative.png"),
    ("mode", "sepia"),
    ("glass_opacity", 2),
])
def test_values_that_would_break_the_page_are_refused(auth_client, field, value):
    # Every value ends up in CSS on four sites; one that could close a url() or is
    # simply out of range must not get that far.
    assert auth_client.post(THEMES, json={**OCEAN, field: value}).status_code == 422


# ─── which theme each app wears ──────────────────────────────────────────────


def test_each_app_keeps_its_own_choice(auth_client):
    ocean = make(auth_client)
    paper = make(auth_client, name="Paper", mode="light", bg_type="flat", bg_color1="#fafafa", bg_color2=None)

    assert auth_client.get(f"{THEMES}/selected/notes").json()["data"] == {"app": "notes", "theme": None}
    assert auth_client.put(f"{THEMES}/selected/notes", json={"theme_id": ocean["id"]}).status_code == 200
    assert auth_client.put(f"{THEMES}/selected/gam", json={"theme_id": paper["id"]}).status_code == 200

    assert auth_client.get(f"{THEMES}/selected/notes").json()["data"]["theme"]["name"] == "Ocean"
    assert auth_client.get(f"{THEMES}/selected/gam").json()["data"]["theme"]["name"] == "Paper"
    everything = {s["app"]: (s["theme"] or {}).get("name") for s in auth_client.get(f"{THEMES}/selected").json()["data"]}
    assert everything == {"notes": "Ocean", "gam": "Paper", "gvc": None, "suite": None}

    # Back to the app's plain light or dark.
    assert auth_client.put(f"{THEMES}/selected/gam", json={"theme_id": None}).json()["data"]["theme"] is None


def test_an_unknown_app_is_refused(auth_client):
    assert auth_client.get(f"{THEMES}/selected/photoshop").status_code == 400


def test_deleting_a_theme_unselects_it_everywhere(auth_client):
    ocean = make(auth_client)
    for app in ("notes", "gvc"):
        auth_client.put(f"{THEMES}/selected/{app}", json={"theme_id": ocean["id"]})
    auth_client.delete(f"{THEMES}/{ocean['id']}")
    assert auth_client.get(f"{THEMES}/selected/notes").json()["data"]["theme"] is None
    assert auth_client.get(f"{THEMES}/selected/gvc").json()["data"]["theme"] is None


def test_a_shared_theme_can_be_worn_by_anyone(auth_client, switch_user, admin):
    switch_user(ADMIN_ID)
    house = make(auth_client, scope="instance", name="House")
    switch_user(TEST_USER_ID)
    assert auth_client.put(f"{THEMES}/selected/suite", json={"theme_id": house["id"]}).status_code == 200
    assert auth_client.get(f"{THEMES}/selected/suite").json()["data"]["theme"]["id"] == house["id"]


def test_choosing_needs_a_session(client):
    assert client.get(f"{THEMES}/selected/notes").status_code == 401


# ─── background images ───────────────────────────────────────────────────────

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_an_uploaded_background_is_served_publicly_at_an_absolute_address(auth_client):
    res = auth_client.post(f"{THEMES}/images", content=PNG, headers={"content-type": "image/png"})
    assert res.status_code == 201, res.text
    url = res.json()["data"]["url"]
    assert url.startswith("https://suite.example.test/api/theme-images/") and url.endswith(".png")

    # Fetched by a CSS url() on another app's page: no credentials. The route takes no
    # session dependency at all, so the override that signs `auth_client` in is moot.
    got = TestClient(fastapi_app).get(url.replace("https://suite.example.test", ""))
    assert got.status_code == 200 and got.content == PNG
    assert "immutable" in got.headers["cache-control"]

    # The address is usable in a theme as is.
    assert make(auth_client, bg_type="image", bg_image_url=url)["bg_image_url"] == url


def test_only_images_of_a_sensible_size_are_accepted(auth_client, monkeypatch):
    assert auth_client.post(f"{THEMES}/images", content=b"<svg/>",
                            headers={"content-type": "image/svg+xml"}).status_code == 400
    assert auth_client.post(f"{THEMES}/images", content=b"",
                            headers={"content-type": "image/png"}).status_code == 400
    import app.routers.themes as themes

    monkeypatch.setattr(themes, "MAX_IMAGE_BYTES", 10)
    assert auth_client.post(f"{THEMES}/images", content=PNG,
                            headers={"content-type": "image/png"}).json()["detail"]["code"] == "image_too_large"


@pytest.mark.parametrize("name", ["../suite.db", "short.png", "x" * 30 + ".svg"])
def test_nothing_but_an_uploaded_image_is_served(client, name):
    assert client.get(f"/api/theme-images/{name}").status_code == 404


# ─── called from the apps' pages ─────────────────────────────────────────────


def test_an_app_page_may_choose_with_the_shared_cookie_and_a_stranger_may_not(client, monkeypatch):
    """The apps' pages call cross-origin with the shared cookie. The suite's cookie
    origin check reads CORS_ORIGIN, so the apps' addresses there are what let them write,
    and anything else is refused (a page on another site cannot change your theme)."""
    from datetime import datetime, timedelta, timezone

    from jose import jwt

    monkeypatch.setattr(settings, "cors_origin", "https://gam.example.test")
    token = jwt.encode({"sub": "u-cookie", "username": "u", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                       settings.jwt_secret_key, algorithm="HS256")
    client.cookies.set("gecko_session", token)
    ok = client.put(f"{THEMES}/selected/gam", json={"theme_id": None}, headers={"Origin": "https://gam.example.test"})
    assert ok.status_code == 200, ok.text
    refused = client.put(f"{THEMES}/selected/gam", json={"theme_id": None}, headers={"Origin": "https://evil.example"})
    assert refused.status_code == 403 and refused.json()["detail"]["code"] == "forbidden_origin"


# ─── Notes' themes come across ───────────────────────────────────────────────


def test_notes_themes_and_choices_come_across(session, tmp_path, catalog):
    import json
    import sqlite3

    from sqlmodel import select

    from app.models.theme import Theme, UserPreference
    from app.tools.import_legacy import run

    path = tmp_path / "notes.db"
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE theme (id TEXT PRIMARY KEY, name TEXT, user_id TEXT, is_global BOOLEAN, mode TEXT,
                  bg_type TEXT, bg_color1 TEXT, bg_color2 TEXT, bg_image_url TEXT, bg_image_mode TEXT, bg_blur REAL,
                  glass_opacity REAL, glass_blur REAL, shadow_size REAL, shadow_blur REAL, created_at TEXT)""")
    db.execute("INSERT INTO theme VALUES ('t-global','Ocean Breeze',NULL,1,'light','gradient','#e0f2fe','#bfdbfe',"
               "NULL,'fill',0,0.3,12,4,12,'2026-01-01T00:00:00')")
    db.execute("INSERT INTO theme VALUES ('t-mine','Forest','u1',0,'dark','image','#000',NULL,"
               "'/media/u1/forest.jpg','fill',2,0.4,10,6,14,'2026-01-02T00:00:00')")
    db.execute("CREATE TABLE usersetting (user_id TEXT, key TEXT, value TEXT, PRIMARY KEY (user_id, key))")
    db.execute("INSERT INTO usersetting VALUES ('u1','active_theme_id',?)", (json.dumps("t-mine"),))
    db.execute("INSERT INTO usersetting VALUES ('u2','active_theme_id',?)", (json.dumps("t-mine"),))  # not theirs
    db.commit()
    db.close()

    report = run(session, {"notes": path}, settings.jwt_secret_key, notes_public_url="https://notes.example.test/")
    assert report.themes == 2

    shared, mine = session.get(Theme, "t-global"), session.get(Theme, "t-mine")
    assert (shared.scope, shared.user_id) == ("instance", None)
    assert (mine.scope, mine.user_id, mine.mode) == ("user", "u1", "dark")
    # Drawn on other apps' pages now, so no longer relative to Notes.
    assert mine.bg_image_url == "https://notes.example.test/media/u1/forest.jpg"

    prefs = {(p.user_id, p.key): json.loads(p.value) for p in session.exec(select(UserPreference)).all()}
    assert prefs == {("u1", "theme.notes"): "t-mine"}  # u2 cannot see u1's theme

    again = run(session, {"notes": path}, settings.jwt_secret_key)
    assert again.themes == 0
