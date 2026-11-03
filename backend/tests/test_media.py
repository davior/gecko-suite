"""The fal.ai gateway against a faked fal: what is sent, what is kept, what is billed.

fal's queue is three calls (submit, status, result) plus cancel, and its bill is the
`x-fal-billable-units` header on the result times a price from its pricing API. These
check the key goes where it should and nowhere else, that a job is only ever billed once,
that an unknown cost stays unknown, and that nobody can follow someone else's job.
"""

import json

import httpx
import pytest

from app.media import pricing
from app.models.usage import UsageEvent
from tests.conftest import ADMIN_ID, TEST_USER_ID

QUEUE = "https://queue.fal.run"
ENDPOINT = "fal-ai/flux-pro/v1.1"
APP_ID = "fal-ai/flux-pro"
RESULT = {"images": [{"url": "https://v3.fal.media/files/a.png", "width": 1024, "height": 1024}]}


@pytest.fixture(autouse=True)
def fresh_prices():
    pricing.clear_cache()


def _fal_provider(client, **fields):
    res = client.post("/api/v1/providers", json={"name": "fal.ai", "vendor": "fal",
                                                 "api_key": "fal-key-secret", **fields})
    assert res.status_code == 201, res.text
    return res.json()["data"]["id"]


class Fal:
    """A scripted fal: queue, status, result, cancel and the pricing API."""

    def __init__(self, vendors):
        self.vendors = vendors
        self.state = "IN_QUEUE"
        self.billable = "1"
        self.price = {"prices": [{"endpoint_id": ENDPOINT, "unit_price": 0.04,
                                  "unit": "image", "currency": "USD"}]}
        self.status_extra: dict = {}
        self.result_response = None
        self.submit_body = None
        self.cancels = 0
        vendors.respond = self.handle

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "POST" and url == f"{QUEUE}/{ENDPOINT}":
            self.submit_body = json.loads(request.content)
            return httpx.Response(200, json={
                "request_id": "req-1",
                "status_url": f"{QUEUE}/{APP_ID}/requests/req-1/status",
                "response_url": f"{QUEUE}/{APP_ID}/requests/req-1",
                "cancel_url": f"{QUEUE}/{APP_ID}/requests/req-1/cancel"})
        if url.startswith(f"{QUEUE}/{APP_ID}/requests/req-1/status"):
            return httpx.Response(200, json={"status": self.state, "queue_position": 2, **self.status_extra})
        if request.method == "PUT" and url.endswith("/cancel"):
            self.cancels += 1
            return httpx.Response(202, json={})
        if url == f"{QUEUE}/{APP_ID}/requests/req-1":
            if self.result_response is not None:
                return self.result_response
            headers = {"x-fal-request-id": "req-1"}
            if self.billable is not None:
                headers["x-fal-billable-units"] = self.billable
            return httpx.Response(200, json=RESULT, headers=headers)
        if "/v1/models/pricing" in url:
            return httpx.Response(200, json=self.price)
        raise AssertionError(f"unexpected request: {request.method} {url}")


@pytest.fixture(name="fal")
def fal_fixture(vendors):
    return Fal(vendors)


def _submit(client, **fields):
    body = {"kind": "image", "endpoint_id": ENDPOINT, "input": {"prompt": "a harbour"},
            "subject_ref": "gam:asset:a1", **fields}
    return client.post("/api/v1/ai/media/jobs", json=body)


def _job(client, job_id):
    return client.get(f"/api/v1/ai/media/jobs/{job_id}").json()["data"]


def _events(session):
    session.expire_all()
    return session.query(UsageEvent).all()


# ─── the happy path ──────────────────────────────────────────────────────────


def test_a_job_is_queued_polled_and_billed_once(auth_client, session, fal, vendors):
    _fal_provider(auth_client)
    res = _submit(auth_client)
    assert res.status_code == 202, res.text
    job = res.json()["data"]
    assert job["status"] == "queued" and job["result"] is None

    # What fal received: the caller's own body, signed with fal's `Key` scheme.
    assert fal.submit_body == {"prompt": "a harbour"}
    assert vendors.calls[0].headers["authorization"] == "Key fal-key-secret"

    assert _job(auth_client, job["id"])["queue_position"] == 2
    fal.state = "IN_PROGRESS"
    assert _job(auth_client, job["id"])["status"] == "running"

    fal.state = "COMPLETED"
    done = _job(auth_client, job["id"])
    assert done["status"] == "completed" and done["result"] == RESULT
    # The header's quantity times fal's own price: billed, not estimated.
    assert (done["billable_units"], done["cost"], done["cost_estimated"]) == ("1", 0.04, False)

    # Asking again does not bill again, nor ask fal again.
    calls = len(vendors.calls)
    assert _job(auth_client, job["id"])["cost"] == 0.04
    assert len(vendors.calls) == calls
    [event] = _events(session)
    assert (event.kind, event.vendor, event.model, event.subject_ref) == (
        "image", "fal", ENDPOINT, "gam:asset:a1")
    assert (event.units, event.unit_type, event.cost_estimated) == (1, "images", False)
    assert event.external_ref == "req-1" and event.user_id == TEST_USER_ID


def test_no_header_derives_the_quantity_and_says_it_is_an_estimate(auth_client, session, fal):
    _fal_provider(auth_client)
    fal.billable = None
    fal.price["prices"][0].update(unit="megapixel", unit_price=0.05)
    job = _submit(auth_client).json()["data"]
    fal.state = "COMPLETED"
    done = _job(auth_client, job["id"])
    # 1024 x 1024 is one of fal's megapixels (1024², not 10⁶).
    assert (done["cost"], done["cost_estimated"]) == (0.05, True)


def test_a_cost_nobody_can_name_is_unknown_not_free(auth_client, session, fal):
    _fal_provider(auth_client)
    fal.price = {"prices": []}
    job = _submit(auth_client).json()["data"]
    fal.state = "COMPLETED"
    done = _job(auth_client, job["id"])
    assert done["status"] == "completed" and done["cost"] is None
    [event] = _events(session)
    # The generation is still counted, so a total does not read as complete when it isn't.
    assert event.cost is None and event.units == 1


def test_a_failed_run_is_reported_and_not_billed(auth_client, session, fal):
    _fal_provider(auth_client)
    job = _submit(auth_client).json()["data"]
    fal.state, fal.status_extra = "COMPLETED", {"error": "The prompt was refused"}
    done = _job(auth_client, job["id"])
    assert done["status"] == "failed" and done["error"] == "The prompt was refused"
    assert _events(session) == []


def test_the_models_own_failure_is_final_not_retried(auth_client, session, fal):
    _fal_provider(auth_client)
    job = _submit(auth_client).json()["data"]
    fal.state = "COMPLETED"
    fal.result_response = httpx.Response(500, json={"detail": "NSFW content"},
                                         headers={"x-fal-request-id": "req-1"})
    done = _job(auth_client, job["id"])
    assert done["status"] == "failed" and "could not generate" in done["error"]


def test_a_bad_gateway_on_a_status_check_leaves_the_job_waiting(auth_client, fal, vendors):
    _fal_provider(auth_client)
    job = _submit(auth_client).json()["data"]
    vendors.respond = lambda request: httpx.Response(502, json={})
    assert _job(auth_client, job["id"])["status"] == "queued"


# ─── cancelling ──────────────────────────────────────────────────────────────


def test_cancel_asks_fal_to_stop_and_bills_nothing(auth_client, session, fal):
    _fal_provider(auth_client)
    job = _submit(auth_client).json()["data"]
    res = auth_client.delete(f"/api/v1/ai/media/jobs/{job['id']}")
    assert res.json()["data"]["status"] == "cancelled" and fal.cancels == 1
    # Done is done: a later poll cannot resurrect it.
    fal.state = "COMPLETED"
    assert _job(auth_client, job["id"])["status"] == "cancelled" and _events(session) == []


# ─── which key, and whose job ────────────────────────────────────────────────


def test_no_fal_key_is_a_409_that_says_where_to_add_one(auth_client, fal):
    res = _submit(auth_client)
    assert res.status_code == 409 and res.json()["detail"]["code"] == "ai_not_configured"
    assert "Gecko Suite" in res.json()["detail"]["message"]


def test_a_personal_key_beats_the_shared_one_and_is_billed_to_it(
        auth_client, switch_user, admin, session, fal, vendors):
    switch_user(ADMIN_ID)
    _fal_provider(auth_client, scope="instance", api_key="shared-key")
    switch_user(TEST_USER_ID)
    _fal_provider(auth_client, api_key="my-own-key")
    _submit(auth_client)
    assert vendors.calls[0].headers["authorization"] == "Key my-own-key"


def test_the_shared_key_is_used_when_there_is_no_personal_one(
        auth_client, switch_user, admin, fal, vendors):
    switch_user(ADMIN_ID)
    _fal_provider(auth_client, scope="instance", api_key="shared-key")
    switch_user(TEST_USER_ID)
    assert _submit(auth_client).status_code == 202
    assert vendors.calls[0].headers["authorization"] == "Key shared-key"


def test_a_job_follows_the_key_it_started_on(auth_client, switch_user, admin, fal, vendors):
    switch_user(ADMIN_ID)
    shared = _fal_provider(auth_client, scope="instance", api_key="shared-key")
    switch_user(TEST_USER_ID)
    job = _submit(auth_client).json()["data"]
    mine = _fal_provider(auth_client, api_key="added-later")
    assert mine != shared
    _job(auth_client, job["id"])
    # The poll used the key the request began on, not whichever is preferred now.
    assert vendors.calls[-1].headers["authorization"] == "Key shared-key"


def test_nobody_can_follow_or_stop_someone_elses_job(auth_client, switch_user, fal):
    _fal_provider(auth_client)
    job = _submit(auth_client).json()["data"]
    switch_user("someone-else")
    assert auth_client.get(f"/api/v1/ai/media/jobs/{job['id']}").status_code == 404
    assert auth_client.delete(f"/api/v1/ai/media/jobs/{job['id']}").status_code == 404


# ─── what is refused before fal is asked ─────────────────────────────────────


@pytest.mark.parametrize("endpoint", [
    "../etc/passwd", "fal-ai/flux?x=1", "fal-ai", "fal-ai//x", "https://evil.example/x",
    "fal-ai/a b", "fal-ai/../../x"])
def test_an_endpoint_id_that_could_redirect_the_key_is_refused(auth_client, vendors, endpoint):
    _fal_provider(auth_client)
    calls = len(vendors.calls)
    res = _submit(auth_client, endpoint_id=endpoint)
    assert res.status_code == 400 and res.json()["detail"]["code"] == "bad_endpoint"
    assert len(vendors.calls) == calls


def test_fal_naming_a_queue_url_on_another_host_is_not_followed(auth_client, vendors, fal):
    _fal_provider(auth_client)
    base = fal.handle

    def evil(request):
        if request.method == "POST":
            return httpx.Response(200, json={"request_id": "req-1",
                                             "status_url": "https://evil.example/steal"})
        return base(request)

    vendors.respond = evil
    job = _submit(auth_client).json()["data"]
    _job(auth_client, job["id"])
    assert all(c.url.host != "evil.example" for c in vendors.calls)


def test_fal_errors_are_sentences_with_the_remedy(auth_client, vendors):
    _fal_provider(auth_client)
    vendors.respond = lambda r: httpx.Response(401, json={"detail": "bad key"})
    res = _submit(auth_client)
    assert res.status_code == 502 and res.json()["detail"]["code"] == "upstream_auth"
    assert "Gecko Suite" in res.json()["detail"]["message"]
    assert "fal-key-secret" not in res.text

    vendors.respond = lambda r: httpx.Response(422, json={"detail": [
        {"loc": ["body", "image_size"], "msg": "not a valid size"}]})
    res = _submit(auth_client)
    assert res.status_code == 422 and "image_size: not a valid size" in res.json()["detail"]["message"]

    vendors.respond = lambda r: httpx.Response(403, json={"detail": "Exhausted balance"})
    assert _submit(auth_client).status_code == 402


# ─── an app calling for a person ─────────────────────────────────────────────


def test_an_app_runs_a_job_for_a_person_and_is_credited_to_the_app(
        client, auth_client, switch_user, admin, session, fal):
    switch_user(ADMIN_ID)
    secret = auth_client.post("/api/v1/apps/gam/secret").json()["data"]["secret"]
    switch_user(TEST_USER_ID)
    _fal_provider(auth_client)
    from app.auth import current_user
    from app.main import app as fastapi_app

    fastapi_app.dependency_overrides.pop(current_user, None)
    headers = {"X-Gecko-App": "gam", "X-Gecko-App-Secret": secret, "X-On-Behalf-Of": TEST_USER_ID}

    job = client.post("/api/v1/ai/media/jobs", headers=headers, json={
        "kind": "image", "endpoint_id": ENDPOINT, "input": {"prompt": "x"}}).json()["data"]
    fal.state = "COMPLETED"
    # GET and DELETE are on the allowlist too, or an app could start a job and never see it.
    assert client.get(f"/api/v1/ai/media/jobs/{job['id']}", headers=headers).status_code == 200
    [event] = _events(session)
    assert (event.app, event.user_id) == ("gam", TEST_USER_ID)
    assert client.delete(f"/api/v1/ai/media/jobs/{job['id']}", headers=headers).status_code == 200
