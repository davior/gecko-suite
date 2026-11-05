"""Live dictation: a browser's microphone relayed to Deepgram's streaming API (`/stt/stream`).

Deepgram is a fake upstream that answers each audio chunk with an interim and a final, and
closes when told `CloseStream`. These pin the protocol the apps' clients speak (Notes' own),
who may open the stream, and that the seconds end up in the ledger.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlmodel import select
from starlette.websockets import WebSocketDisconnect

from app.config import settings
from app.database import get_session
from app.main import app as fastapi_app
from app.models.usage import UsageEvent
from app.speech import live
from tests.conftest import TEST_USER_ID

STREAM = "/api/v1/ai/speech/stt/stream"
SAME_ORIGIN = {"origin": "http://testserver"}


def token(sub=TEST_USER_ID, **claims):
    return jwt.encode({"sub": sub, "username": sub, "exp": datetime.now(timezone.utc) + timedelta(hours=1),
                       **claims}, settings.jwt_secret_key, algorithm="HS256")


class FakeDeepgram:
    """Hears each chunk as a word: an interim, then a final. Reports the seconds and closes
    on CloseStream, as Deepgram does."""

    def __init__(self):
        self.received: list = []
        self.url = None
        self.key = None
        self.closed = False
        self._out: asyncio.Queue = asyncio.Queue()

    async def send(self, data):
        self.received.append(data)
        if isinstance(data, bytes):
            word = data.decode()
            await self._out.put(json.dumps({"type": "Results", "is_final": False,
                                            "channel": {"alternatives": [{"transcript": word[:2]}]}}))
            await self._out.put(json.dumps({"type": "Results", "is_final": True,
                                            "channel": {"alternatives": [{"transcript": word}]}}))
        elif json.loads(data).get("type") == "CloseStream":
            await self._out.put(json.dumps({"type": "Metadata", "duration": 4.2}))
            await self._out.put(None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._out.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def close(self):
        self.closed = True


@pytest.fixture
def fake(monkeypatch):
    upstream = FakeDeepgram()

    async def open_upstream(url, key):
        upstream.url, upstream.key = url, key
        return upstream

    monkeypatch.setattr(live, "open_upstream", open_upstream)
    return upstream


@pytest.fixture
def ws_client(session):
    """No `current_user` override: the stream checks the token itself, as a browser sends it."""
    fastapi_app.dependency_overrides[get_session] = lambda: session
    with TestClient(fastapi_app) as client:
        yield client
    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def deepgram_key(auth_client):
    res = auth_client.post("/api/v1/providers", json={"name": "Deepgram", "vendor": "deepgram",
                                                      "api_key": "dg-key-secret"})
    assert res.status_code == 201, res.text


def closed_with(client, url, headers=SAME_ORIGIN):
    with client.websocket_connect(url, headers=headers) as ws:
        with pytest.raises(WebSocketDisconnect) as info:
            ws.receive_json()
    return info.value.code


def test_words_come_back_as_they_are_heard_and_the_seconds_are_recorded(deepgram_key, ws_client, fake, session):
    with ws_client.websocket_connect(f"{STREAM}?token={token()}&app=gvc", headers=SAME_ORIGIN) as ws:
        ws.send_bytes(b"hello")
        assert ws.receive_json() == {"type": "interim", "text": "he"}
        assert ws.receive_json() == {"type": "final", "text": "hello"}
        ws.send_text(json.dumps({"type": "stop"}))
        # The suite closes once Deepgram has flushed; wait for it rather than racing it.
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()

    # The key went to Deepgram in its own scheme, never to the browser.
    assert fake.key == "dg-key-secret" and "interim_results=true" in fake.url and "model=nova-3" in fake.url
    assert fake.received[-1] == json.dumps({"type": "CloseStream"}) and fake.closed

    (event,) = session.exec(select(UsageEvent)).all()
    assert (event.user_id, event.app, event.task, event.units, event.unit_type) == (
        TEST_USER_ID, "gvc", "speech.stt", 5, "seconds")
    assert event.cost is None  # unknown, never 0


@pytest.mark.parametrize("why, query", [
    ("no token", ""),
    ("a token that does not verify", "token=not-a-jwt"),
    ("a two-factor challenge", f"token={token(twofa_pending=True)}"),
])
def test_without_a_session_the_stream_is_refused(ws_client, fake, why, query):
    assert closed_with(ws_client, f"{STREAM}?{query}") == live.CLOSE_UNAUTHORIZED
    assert fake.url is None


def test_the_shared_session_cookie_is_enough(deepgram_key, ws_client, fake):
    # GVC keeps no token of its own: the browser sends the parent-domain cookie with the
    # handshake, and that must sign the stream in.
    ws_client.cookies.set("gecko_session", token())
    with ws_client.websocket_connect(f"{STREAM}?app=gvc", headers=SAME_ORIGIN) as ws:
        ws.send_bytes(b"hi")
        assert ws.receive_json() == {"type": "interim", "text": "hi"}


def test_the_cookie_does_not_let_another_site_in(deepgram_key, ws_client, fake):
    ws_client.cookies.set("gecko_session", token())
    code = closed_with(ws_client, f"{STREAM}?app=gvc", headers={"origin": "https://evil.example"})
    assert code == live.CLOSE_FORBIDDEN and fake.url is None


def test_a_page_from_another_site_is_refused(deepgram_key, ws_client, fake):
    # CORS does not cover websockets: this is what stops a stranger's page using a token.
    code = closed_with(ws_client, f"{STREAM}?token={token()}", headers={"origin": "https://evil.example"})
    assert code == live.CLOSE_FORBIDDEN and fake.url is None


def test_an_app_in_cors_origin_is_let_in(deepgram_key, ws_client, fake, monkeypatch):
    monkeypatch.setattr(settings, "cors_origin", "https://gvc.example.test")
    with ws_client.websocket_connect(f"{STREAM}?token={token()}&app=gvc",
                                     headers={"origin": "https://gvc.example.test"}) as ws:
        ws.send_bytes(b"ok")
        assert ws.receive_json()["type"] == "interim"


def test_with_no_deepgram_key_it_says_so(ws_client, fake):
    assert closed_with(ws_client, f"{STREAM}?token={token()}") == live.CLOSE_NO_KEY


def test_one_live_dictation_at_a_time(deepgram_key, ws_client, fake):
    from app.routers import speech

    speech._live_sessions.add(TEST_USER_ID)
    try:
        assert closed_with(ws_client, f"{STREAM}?token={token()}") == live.CLOSE_BUSY
    finally:
        speech._live_sessions.discard(TEST_USER_ID)


def test_an_unknown_app_is_refused(deepgram_key, ws_client, fake):
    assert closed_with(ws_client, f"{STREAM}?token={token()}&app=photoshop") == live.CLOSE_FORBIDDEN


def test_the_listen_address_follows_the_configured_deepgram(monkeypatch):
    monkeypatch.setattr(settings, "deepgram_base_url", "https://dg.example.test/")
    assert live.listen_url("nova-3", "en").startswith("wss://dg.example.test/v1/listen?model=nova-3")
    assert live.listen_url("nova-3", "en").endswith("&language=en")
