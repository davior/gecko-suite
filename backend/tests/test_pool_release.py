"""A model call must not hold a database connection while the vendor answers.

The gateway reads the routing, then waits on a vendor for seconds or minutes. A request's
session that has read something keeps its pooled connection until it commits or closes,
so every call in flight held one: fifteen at once (SQLAlchemy's default pool, 5 + 10
overflow) and the next request of any kind, even `/resolve`, waited 30 seconds for a free
connection and failed as a bare 500. GVC's assistant, picking clips four at a time while
GAM embeds its searches, got there.

The rest of the tests share one in-memory session, which can never show this, so these
run against a file database with a one-connection pool and a session per request, as
production does, and a vendor that answers only when told to.
"""

import threading

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from app.auth import UserCtx, current_user
from app.database import get_session
from app.main import app as fastapi_app
from tests.conftest import TEST_USER_ID


@pytest.fixture(name="pooled")
def pooled_fixture(tmp_path):
    """A client whose every request gets its own session on a one-connection pool that
    gives up after a second: a held connection shows as a failure, not a 30 s wait."""
    engine = create_engine(f"sqlite:///{tmp_path / 'suite.db'}",
                           connect_args={"check_same_thread": False},
                           pool_size=1, max_overflow=0, pool_timeout=1)

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(engine)

    def per_request():
        with Session(engine) as session:
            yield session

    fastapi_app.dependency_overrides[get_session] = per_request
    fastapi_app.dependency_overrides[current_user] = lambda: UserCtx(id=TEST_USER_ID,
                                                                    username=TEST_USER_ID)
    with Session(engine) as seed:
        _seed_catalog(seed)
    with TestClient(fastapi_app) as client:
        client.engine = engine
        yield client
    fastapi_app.dependency_overrides.clear()
    engine.dispose()


def _seed_catalog(session):
    """The catalogue as the seed migration writes it, as the `catalog` fixture does."""
    import importlib.util
    import json
    from pathlib import Path

    from app.models.ai import CatalogEntry

    path = next(Path(__file__).parents[1].glob("alembic/versions/*seed_the_model_catalogue.py"))
    spec = importlib.util.spec_from_file_location("seed_catalogue", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for index, (vendor, model_id, label, caps, max_tokens, context, dims, pricing, note) in enumerate(
            module.ROWS):
        session.add(CatalogEntry(
            vendor=vendor, model_id=model_id, label=label, capabilities=json.dumps(sorted(caps)),
            max_tokens=max_tokens, context_window=context, embed_dims=dims,
            pricing=json.dumps(pricing, sort_keys=True) if pricing else None, note=note,
            sort_order=index))
    session.commit()


class Gate:
    """A vendor that holds every request until `release`, and says when one arrived."""

    def __init__(self, answer):
        self.arrived = threading.Event()
        self.go = threading.Event()
        self.answer = answer

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.arrived.set()
        assert self.go.wait(10), "the test never released the vendor"
        return self.answer(request)


@pytest.fixture(name="gate")
def gate_fixture(monkeypatch):
    from app import outbound

    gate = Gate(lambda r: httpx.Response(200, json={}))
    monkeypatch.setattr(outbound, "TRANSPORT", httpx.MockTransport(gate.handler))
    return gate


def _route(client, vendor, model_id, task):
    pid = client.post("/api/v1/providers", json={"name": vendor, "vendor": vendor,
                                                 "api_key": "k"}).json()["data"]["id"]
    mid = client.post(f"/api/v1/providers/{pid}/models", json={"model_id": model_id}).json()["data"]["id"]
    assert client.put("/api/v1/assignments", json={"task": task, "model_ref": mid}).status_code == 200


def _while_the_vendor_thinks(client, gate, call):
    """Start `call` in a thread, wait until it reaches the vendor, then ask what a turn
    asks first. Returns that answer and, once the vendor is released, the call's."""
    result = {}
    worker = threading.Thread(target=lambda: result.setdefault("call", call()))
    worker.start()
    try:
        assert gate.arrived.wait(5), "the call never reached the vendor"
        meanwhile = client.get("/api/v1/resolve", params={"task": "chat.assistant"})
    finally:
        gate.go.set()
        worker.join(10)
    return meanwhile, result["call"]


ANTHROPIC = lambda r: httpx.Response(200, json={  # noqa: E731
    "model": "claude-opus-5-5", "content": [{"type": "text", "text": "Hello."}],
    "stop_reason": "end_turn", "usage": {"input_tokens": 10, "output_tokens": 5}})


def test_a_chat_call_frees_its_connection_while_the_vendor_answers(pooled, gate):
    _route(pooled, "anthropic", "claude-opus-5-5", "chat.assistant")
    gate.answer = ANTHROPIC
    body = {"task": "chat.assistant", "messages": [{"role": "user", "content": "Hi"}]}

    meanwhile, done = _while_the_vendor_thinks(pooled, gate,
                                               lambda: pooled.post("/api/v1/ai/chat", json=body))

    assert meanwhile.status_code == 200, meanwhile.text
    assert done.status_code == 200, done.text
    # The usage is still recorded at the end, on a connection taken back for it.
    from sqlmodel import select

    from app.models.usage import UsageEvent

    with Session(pooled.engine) as session:
        assert len(session.exec(select(UsageEvent)).all()) == 1


def test_a_streamed_chat_frees_its_connection_too(pooled, gate):
    _route(pooled, "anthropic", "claude-opus-5-5", "chat.assistant")
    sse = ("event: message_start\ndata: {\"type\":\"message_start\",\"message\":{\"model\":"
           "\"claude-opus-5-5\",\"usage\":{\"input_tokens\":10,\"output_tokens\":0}}}\n\n"
           "event: content_block_start\ndata: {\"type\":\"content_block_start\",\"index\":0,"
           "\"content_block\":{\"type\":\"text\",\"text\":\"\"}}\n\n"
           "event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"index\":0,"
           "\"delta\":{\"type\":\"text_delta\",\"text\":\"Hello.\"}}\n\n"
           "event: content_block_stop\ndata: {\"type\":\"content_block_stop\",\"index\":0}\n\n"
           "event: message_delta\ndata: {\"type\":\"message_delta\",\"delta\":{\"stop_reason\":"
           "\"end_turn\"},\"usage\":{\"output_tokens\":5}}\n\n"
           "event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n")
    gate.answer = lambda r: httpx.Response(200, text=sse,
                                           headers={"content-type": "text/event-stream"})
    body = {"task": "chat.assistant", "messages": [{"role": "user", "content": "Hi"}],
            "stream": True}

    meanwhile, done = _while_the_vendor_thinks(pooled, gate,
                                               lambda: pooled.post("/api/v1/ai/chat", json=body))

    assert meanwhile.status_code == 200, meanwhile.text
    assert done.status_code == 200 and "event: final" in done.text, done.text


def test_an_embedding_frees_its_connection_while_the_vendor_answers(pooled, gate):
    _route(pooled, "openai", "text-embedding-3-small", "embed.text")
    _route(pooled, "anthropic", "claude-opus-5-5", "chat.assistant")
    gate.answer = lambda r: httpx.Response(200, json={
        "data": [{"index": 0, "embedding": [0.1, 0.2]}],
        "usage": {"prompt_tokens": 2, "total_tokens": 2}})
    body = {"task": "embed.text", "texts": ["owl"], "app": "gam"}

    meanwhile, done = _while_the_vendor_thinks(pooled, gate,
                                               lambda: pooled.post("/api/v1/ai/embed", json=body))

    assert meanwhile.status_code == 200, meanwhile.text
    assert done.status_code == 200, done.text


def test_an_exhausted_pool_is_said_as_the_suite_being_busy(pooled):
    """If it happens anyway, the apps get a 503 they turn into a sentence, not a bare 500."""
    held = pooled.engine.connect()
    try:
        res = pooled.get("/api/v1/resolve", params={"task": "chat.assistant"})
    finally:
        held.close()
    assert res.status_code == 503
    assert res.json()["detail"]["code"] == "suite_unavailable"
