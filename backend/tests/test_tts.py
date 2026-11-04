"""Text to speech through the routed `speech.tts` model, against faked fal and Deepgram.

What is checked: the request each engine's dialect asks for, which model and voice a call
resolves to and that a narration can pin them, that a preview then a full render bills once,
that an unknown cost stays unknown, and that nothing can aim the fal key at an audio host.
"""

import json

import httpx
import pytest
from fastapi import HTTPException

from app.config import settings
from app.media import pricing
from app.models.ai import AIModel, AIProvider
from app.models.usage import UsageEvent
from app.speech import tts
from tests.conftest import ADMIN_ID, TEST_USER_ID

MP3 = b"ID3" + b"\x00" * 400
FAL_ENDPOINT = "fal-ai/elevenlabs/tts/eleven-v3"
AUDIO_URL = "https://v3.fal.media/files/narration.mp3"
VOICES = [{"id": "Aria"}, {"id": "Roger"}, "Sarah"]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    pricing.clear_cache()
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(tts, "require_safe_external_url", lambda url: None)


def _model(client, vendor, model_id, *, caps=("tts",), voices=None, params=None, **provider):
    key = {"fal": "fal-key-secret", "deepgram": "dg-key-secret"}[vendor]
    res = client.post("/api/v1/providers", json={"name": vendor, "vendor": vendor, "api_key": key,
                                                 **provider})
    assert res.status_code == 201, res.text
    pid = res.json()["data"]["id"]
    body = {"model_id": model_id, "from_catalog": False, "capabilities": list(caps)}
    if voices is not None:
        body["voices"] = voices
    if params is not None:
        body["params"] = params
    res = client.post(f"/api/v1/providers/{pid}/models", json=body)
    assert res.status_code == 201, res.text
    return res.json()["data"]["id"]


def _route(client, model_ref, **fields):
    res = client.put("/api/v1/assignments", json={"task": "speech.tts", "model_ref": model_ref, **fields})
    assert res.status_code == 200, res.text


def _fal_model(client, **extra):
    params = {"text_field": "prompt", "voice_field": "voice_id", "default_voice": "Aria",
              "extra_params": {"stability": 0.5}, **extra}
    return _model(client, "fal", FAL_ENDPOINT, voices=VOICES, params=params)


def _speak(client, text="Hello there.", **fields):
    return client.post("/api/v1/ai/speech/tts", json={"text": text, **fields})


class Vendors:
    """fal's synchronous endpoint, its audio host, its pricing API, and Deepgram's /v2/speak."""

    def __init__(self, vendors):
        self.vendors = vendors
        self.audio = MP3
        self.billable = "0.5"
        self.price = {"prices": [{"endpoint_id": FAL_ENDPOINT, "unit_price": 0.1,
                                  "unit": "1000 characters", "currency": "USD"}]}
        self.fal_response = None
        vendors.respond = self.handle

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.url.host == "fal.run":
            if self.fal_response is not None:
                return self.fal_response
            headers = {"x-fal-request-id": "req-9"}
            if self.billable is not None:
                headers["x-fal-billable-units"] = self.billable
            return httpx.Response(200, json={"audio": {"url": AUDIO_URL, "content_type": "audio/mpeg"}},
                                  headers=headers)
        if url == AUDIO_URL:
            return httpx.Response(200, content=self.audio, headers={"content-type": "audio/mpeg"})
        if "/v1/models/pricing" in url:
            return httpx.Response(200, json=self.price)
        if request.url.host == "api.deepgram.com" and request.url.path == "/v2/speak":
            return httpx.Response(200, content=self.audio)
        raise AssertionError(f"unexpected request: {request.method} {url}")

    def calls_to(self, host):
        return [c for c in self.vendors.calls if c.url.host == host]


@pytest.fixture(name="speech")
def speech_fixture(vendors):
    return Vendors(vendors)


def _events(session):
    session.expire_all()
    return session.query(UsageEvent).all()


# ─── fal ─────────────────────────────────────────────────────────────────────


def test_fal_reads_the_text_in_the_endpoints_own_dialect_and_bills_exactly(
        auth_client, session, speech):
    model = _fal_model(auth_client)
    _route(auth_client, model)
    res = _speak(auth_client, voice="Roger", subject_ref="gvc:project:p1")

    assert res.status_code == 200, res.text
    assert res.content == MP3 and res.headers["content-type"] == "audio/mpeg"
    # What resolved comes back, for the caller to pin.
    assert (res.headers["x-speech-provider"], res.headers["x-speech-model"],
            res.headers["x-speech-voice"], res.headers["x-speech-cached"]) == ("fal", model, "Roger", "false")

    (call,) = speech.calls_to("fal.run")
    assert str(call.url) == f"https://fal.run/{FAL_ENDPOINT}"
    assert call.headers["authorization"] == "Key fal-key-secret"
    # The model row said which fields: `prompt` and `voice_id`, plus its extras.
    assert json.loads(call.content) == {"prompt": "Hello there.", "voice_id": "Roger", "stability": 0.5}
    # The audio host never sees the key.
    (download,) = [c for c in speech.vendors.calls if str(c.url) == AUDIO_URL]
    assert "authorization" not in download.headers

    [event] = _events(session)
    assert (event.kind, event.task, event.vendor, event.model, event.subject_ref) == (
        "tts", "speech.tts", "fal", FAL_ENDPOINT, "gvc:project:p1")
    # Characters recorded; fal's billed 0.5 units times its 0.1 price: exact.
    assert (event.units, event.unit_type, event.cost, event.cost_estimated) == (12, "chars", 0.05, False)
    assert event.user_id == TEST_USER_ID and "fal-key-secret" not in res.text


def test_an_unknown_voice_is_replaced_by_the_models_default(auth_client, speech):
    _route(auth_client, _fal_model(auth_client))
    res = _speak(auth_client, voice="Stale Voice From Another Model")
    assert res.headers["x-speech-voice"] == "Aria"
    assert json.loads(speech.calls_to("fal.run")[0].content)["voice_id"] == "Aria"
    # A voice the model offers as a bare string is as good as one given as an object.
    assert _speak(auth_client, text="Another.", voice="Sarah").headers["x-speech-voice"] == "Sarah"


def test_no_voice_means_the_models_default(auth_client, speech):
    _route(auth_client, _fal_model(auth_client))
    assert _speak(auth_client).headers["x-speech-voice"] == "Aria"


def test_without_a_billed_quantity_the_cost_is_unknown_not_free(auth_client, session, speech):
    speech.billable = None
    _route(auth_client, _fal_model(auth_client))
    _speak(auth_client)
    [event] = _events(session)
    assert event.cost is None and event.units == 12


# ─── Deepgram ────────────────────────────────────────────────────────────────


def test_deepgram_uses_the_voice_as_its_model_and_the_models_settings(auth_client, session, speech):
    model = _model(auth_client, "deepgram", "deepgram-flux",
                   voices=["flux-haley-en", "flux-kit-en"],
                   params={"default_voice": "flux-haley-en", "expressivity": 1, "speed": 1.1})
    _route(auth_client, model)
    res = _speak(auth_client, voice="flux-kit-en")
    assert res.status_code == 200 and res.headers["x-speech-provider"] == "deepgram"
    assert res.headers["x-speech-voice"] == "flux-kit-en"

    (call,) = speech.calls_to("api.deepgram.com")
    assert call.headers["authorization"] == "Token dg-key-secret"
    assert json.loads(call.content) == {"text": "Hello there."}
    assert dict(call.url.params) == {"model": "flux-kit-en", "encoding": "mp3",
                                     "expressivity": "1", "speed": "1.10"}
    [event] = _events(session)
    # Deepgram sends no billed quantity: the characters are recorded, the cost unknown.
    assert (event.vendor, event.model, event.units, event.cost) == ("deepgram", "flux-kit-en", 12, None)


def test_settings_deepgram_would_reject_fall_back_to_the_defaults(auth_client, speech):
    model = _model(auth_client, "deepgram", "deepgram-flux", voices=["flux-haley-en"],
                   params={"expressivity": 9, "speed": 3.0})
    _route(auth_client, model)
    _speak(auth_client)
    assert dict(speech.calls_to("api.deepgram.com")[0].url.params)["expressivity"] == "0"
    assert dict(speech.calls_to("api.deepgram.com")[0].url.params)["speed"] == "1.00"


def test_a_voice_name_that_could_alter_the_request_is_refused(auth_client, speech):
    model = _model(auth_client, "deepgram", "deepgram-flux", params={"default_voice": "x&callback=evil"})
    _route(auth_client, model)
    res = _speak(auth_client)
    assert res.status_code == 400 and res.json()["detail"]["code"] == "bad_voice"
    assert speech.calls_to("api.deepgram.com") == []


# ─── the cache ───────────────────────────────────────────────────────────────


def test_a_second_read_of_the_same_words_costs_nothing(auth_client, session, speech):
    _route(auth_client, _fal_model(auth_client))
    first = _speak(auth_client)
    calls = len(speech.vendors.calls)
    second = _speak(auth_client)

    assert second.content == MP3 and second.headers["x-speech-cached"] == "true"
    # Still names what resolved, so a render's first chunk can be a cache hit and still pin.
    assert second.headers["x-speech-model"] == first.headers["x-speech-model"]
    assert len(speech.vendors.calls) == calls and len(_events(session)) == 1


def test_a_different_voice_or_different_words_are_not_the_same_audio(auth_client, session, speech):
    _route(auth_client, _fal_model(auth_client))
    _speak(auth_client)
    _speak(auth_client, voice="Roger")
    _speak(auth_client, text="Other words.")
    assert len(speech.calls_to("fal.run")) == 3 and len(_events(session)) == 3


def test_the_cache_key_changes_with_the_key_it_was_bought_with(auth_client, session):
    model = session.get(AIModel, _fal_model(auth_client))
    one = session.get(AIProvider, model.provider_id)
    other = AIProvider(scope="user", user_id="x", name="fal", vendor="fal")
    assert tts._cache_key(one, model, {}, "Aria", "hi") != tts._cache_key(other, model, {}, "Aria", "hi")
    assert tts._cache_key(one, model, {"speed": 1.0}, "Aria", "hi") != tts._cache_key(one, model, {"speed": 1.1}, "Aria", "hi")


def test_a_cached_body_too_small_to_be_audio_is_read_again(auth_client, session, speech, tmp_path):
    model = _fal_model(auth_client)
    _route(auth_client, model)
    row = session.get(AIModel, model)
    key = tts._cache_key(session.get(AIProvider, row.provider_id), row, json.loads(row.params), "Aria", "Hello there.")
    tts._cache_put(key, b"tiny")
    res = _speak(auth_client)
    assert res.headers["x-speech-cached"] == "false" and res.content == MP3


# ─── which model ─────────────────────────────────────────────────────────────


def test_a_narration_pins_the_model_it_began_on_even_if_routing_changes(auth_client, speech):
    first = _fal_model(auth_client)
    _route(auth_client, first)
    pinned = _speak(auth_client).headers["x-speech-model"]
    second = _model(auth_client, "deepgram", "deepgram-flux", voices=["flux-haley-en"],
                    params={"default_voice": "flux-haley-en"})
    _route(auth_client, second)

    res = _speak(auth_client, text="Next chunk.", model_ref=pinned, voice="Aria")
    # Routing now points at Deepgram; the pin keeps this narration on the voice it began with.
    assert res.headers["x-speech-provider"] == "fal"
    # A change of routing is what a *new* narration sees.
    assert _speak(auth_client, text="A new one.").headers["x-speech-provider"] == "deepgram"


def test_a_pinned_model_that_fails_is_an_error_not_a_change_of_voice(auth_client, speech):
    _route(auth_client, _fal_model(auth_client))
    pinned = _speak(auth_client).headers["x-speech-model"]
    speech.fal_response = httpx.Response(500, json={"detail": "down"})
    res = _speak(auth_client, text="Different words.", model_ref=pinned)
    assert res.status_code == 503 and res.json()["detail"]["code"] == "upstream_unavailable"


def test_nothing_routed_says_what_to_set_up(auth_client, speech):
    res = _speak(auth_client)
    assert res.status_code == 409 and res.json()["detail"]["code"] == "ai_not_configured"
    assert "speech.tts" in res.json()["detail"]["message"] and speech.vendors.calls == []


def test_a_model_without_the_tts_capability_is_refused(auth_client, speech):
    model = _model(auth_client, "fal", FAL_ENDPOINT, caps=("image_gen",))
    res = _speak(auth_client, model_ref=model)
    assert res.status_code == 400 and res.json()["detail"]["code"] == "missing_capability"


def test_someone_elses_personal_model_cannot_be_pinned(auth_client, switch_user, speech):
    switch_user("someone-else")
    theirs = _fal_model(auth_client)
    switch_user(TEST_USER_ID)
    assert _speak(auth_client, model_ref=theirs).status_code == 404


# ─── what is refused or fails ────────────────────────────────────────────────


def test_empty_and_overlong_text_never_reach_the_vendor(auth_client, speech, monkeypatch):
    _route(auth_client, _fal_model(auth_client))
    assert _speak(auth_client, text="   ").json()["detail"]["code"] == "empty_text"
    monkeypatch.setattr(settings, "tts_max_chars", 10)
    assert _speak(auth_client, text="x" * 11).json()["detail"]["code"] == "text_too_long"
    assert speech.vendors.calls == []


def test_audio_too_small_to_be_audio_is_an_error_and_is_not_kept_or_billed(auth_client, session, speech):
    speech.audio = b"nope"
    _route(auth_client, _fal_model(auth_client))
    res = _speak(auth_client)
    assert res.status_code == 502 and res.json()["detail"]["code"] == "audio_empty"
    assert _events(session) == []
    speech.audio = MP3
    assert _speak(auth_client).headers["x-speech-cached"] == "false"


def test_an_audio_address_the_service_will_not_fetch_is_not_fetched(auth_client, speech, monkeypatch):
    def refuse(url):
        raise HTTPException(status_code=400, detail={"code": "ssrf_blocked", "message": "no"})

    monkeypatch.setattr(tts, "require_safe_external_url", refuse)
    _route(auth_client, _fal_model(auth_client))
    res = _speak(auth_client)
    assert res.status_code == 502 and "will not fetch" in res.json()["detail"]["message"]
    assert [c for c in speech.vendors.calls if str(c.url) == AUDIO_URL] == []


def test_fals_refusals_are_sentences_with_the_remedy(auth_client, speech):
    _route(auth_client, _fal_model(auth_client))
    speech.fal_response = httpx.Response(401, json={"detail": "bad key"})
    res = _speak(auth_client)
    assert res.status_code == 502 and res.json()["detail"]["code"] == "upstream_auth"
    assert "fal-key-secret" not in res.text and "Gecko Suite" in res.json()["detail"]["message"]


# ─── an app calling for a person ─────────────────────────────────────────────


def test_an_app_reads_text_for_a_person_and_is_credited(
        client, auth_client, switch_user, admin, session, speech):
    switch_user(ADMIN_ID)
    secret = auth_client.post("/api/v1/apps/gvc/secret").json()["data"]["secret"]
    switch_user(TEST_USER_ID)
    _route(auth_client, _fal_model(auth_client))
    from app.auth import current_user
    from app.main import app as fastapi_app

    fastapi_app.dependency_overrides.pop(current_user, None)
    headers = {"X-Gecko-App": "gvc", "X-Gecko-App-Secret": secret, "X-On-Behalf-Of": TEST_USER_ID}
    res = client.post("/api/v1/ai/speech/tts", headers=headers, json={"text": "Narration."})
    assert res.status_code == 200, res.text
    [event] = _events(session)
    assert (event.app, event.user_id) == ("gvc", TEST_USER_ID)
