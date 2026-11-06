"""Speech to text against a faked Deepgram: what is sent, what comes back, what is recorded.

The audio is the request body, the key is the caller's visible Deepgram provider, and the
seconds go to the ledger with an unknown cost (never 0, since the suite has no price for
Deepgram's minutes).
"""

import httpx
import pytest

from app.config import settings
from app.models.usage import UsageEvent
from tests.conftest import ADMIN_ID, TEST_USER_ID

AUDIO = b"fLaC" + b"\x00" * 64

UTTERANCES = {
    "metadata": {"duration": 12.3, "model_info": {"abc-uuid": {"name": "nova-3-general"}}},
    "results": {
        "channels": [{"detected_language": "en", "alternatives": [{"transcript": "hello there"}]}],
        "utterances": [
            {"transcript": " Hello there. ", "start": 0.1, "end": 1.4, "speaker": 0,
             "words": [{"word": "hello", "punctuated_word": "Hello", "start": 0.1, "end": 0.5},
                       {"word": "there", "punctuated_word": "there.", "start": 0.6, "end": 1.4}]},
            {"transcript": "   ", "start": 2, "end": 3},
            {"transcript": "Right.", "start": 5.0, "end": 5.6, "speaker": 1, "words": []},
        ],
    },
}


def _deepgram_provider(client, **fields):
    res = client.post("/api/v1/providers", json={"name": "Deepgram", "vendor": "deepgram",
                                                 "api_key": "dg-key-secret", **fields})
    assert res.status_code == 201, res.text
    return res.json()["data"]["id"]


def _transcribe(client, audio=AUDIO, content_type="audio/flac", **params):
    return client.post("/api/v1/ai/speech/stt", params=params, content=audio,
                       headers={"content-type": content_type})


@pytest.fixture(name="deepgram")
def deepgram_fixture(vendors):
    vendors.respond = lambda request: httpx.Response(200, json=UTTERANCES)
    return vendors


def _events(session):
    session.expire_all()
    return session.query(UsageEvent).all()


def test_audio_in_a_transcript_with_words_and_speakers_out(auth_client, session, deepgram):
    _deepgram_provider(auth_client)
    res = _transcribe(auth_client, subject_ref="gam:asset:a1")
    assert res.status_code == 200, res.text
    data = res.json()["data"]

    # Blank utterances are dropped, punctuation survives, speakers are kept.
    assert [s["text"] for s in data["segments"]] == ["Hello there.", "Right."]
    assert data["segments"][0]["speaker"] == 0 and data["segments"][1]["speaker"] == 1
    assert data["segments"][0]["words"][1] == {"text": "there.", "start": 0.6, "end": 1.4}
    assert (data["language"], data["model"], data["duration_seconds"]) == ("en", "nova-3-general", 12.3)

    # What Deepgram received: the audio as the body, the key in Deepgram's own scheme, and
    # the parameters that make segments useful.
    (call,) = deepgram.calls
    assert call.url.host == "api.deepgram.com" and call.url.path == "/v1/listen"
    assert call.headers["authorization"] == "Token dg-key-secret"
    assert call.headers["content-type"] == "audio/flac" and call.content == AUDIO
    query = dict(call.url.params)
    assert query["model"] == "nova-3" and query["utterances"] == "true" and query["diarize"] == "true"
    assert query["detect_language"] == "true" and "language" not in query

    [event] = _events(session)
    assert (event.kind, event.vendor, event.task, event.subject_ref) == (
        "stt", "deepgram", "speech.stt", "gam:asset:a1")
    # Rounded up to whole seconds, and the cost unknown rather than free.
    assert (event.units, event.unit_type, event.cost, event.model) == (13, "seconds", None, "nova-3-general")
    assert event.user_id == TEST_USER_ID and "dg-key-secret" not in res.text


def test_a_named_language_replaces_detection(auth_client, deepgram):
    _deepgram_provider(auth_client)
    _transcribe(auth_client, language="it", model="nova-2")
    query = dict(deepgram.calls[0].url.params)
    assert query["language"] == "it" and query["model"] == "nova-2" and "detect_language" not in query


def test_a_response_without_utterances_still_gives_a_transcript(auth_client, deepgram):
    _deepgram_provider(auth_client)
    deepgram.respond = lambda r: httpx.Response(200, json={
        "metadata": {}, "results": {"channels": [{"alternatives": [{
            "transcript": "just words", "words": [{"word": "just", "start": 0.0, "end": 0.4},
                                                  {"word": "words", "start": 0.5, "end": 0.9}]}]}]}})
    data = _transcribe(auth_client).json()["data"]
    assert data["segments"][0]["text"] == "just words" and data["segments"][0]["end"] == 0.9
    # No duration in the metadata: the last word's end stands in for it.
    assert data["duration_seconds"] == 0.9


def test_readiness_says_whether_a_key_would_be_found(auth_client):
    assert auth_client.get("/api/v1/ai/speech/stt/ready").json()["data"] == {
        "ready": False, "provider": None}
    _deepgram_provider(auth_client)
    assert auth_client.get("/api/v1/ai/speech/stt/ready").json()["data"] == {
        "ready": True, "provider": "Deepgram"}


def test_no_key_is_a_409_that_says_where_to_add_one(auth_client, deepgram):
    res = _transcribe(auth_client)
    assert res.status_code == 409 and res.json()["detail"]["code"] == "ai_not_configured"
    assert "Gecko Suite" in res.json()["detail"]["message"] and deepgram.calls == []


def test_the_shared_key_serves_a_person_with_none_of_their_own(
        auth_client, switch_user, admin, deepgram):
    switch_user(ADMIN_ID)
    _deepgram_provider(auth_client, scope="instance", api_key="shared-key")
    switch_user(TEST_USER_ID)
    assert _transcribe(auth_client).status_code == 200
    assert deepgram.calls[0].headers["authorization"] == "Token shared-key"


def test_someone_elses_personal_key_is_never_used(auth_client, switch_user, deepgram):
    switch_user("someone-else")
    _deepgram_provider(auth_client)
    switch_user(TEST_USER_ID)
    assert _transcribe(auth_client).status_code == 409 and deepgram.calls == []


@pytest.mark.parametrize("status,expected_status,code", [
    (401, 502, "upstream_auth"), (402, 402, "upstream_quota"), (400, 422, "upstream_invalid"),
    (500, 502, "upstream_error")])
def test_deepgrams_refusals_are_sentences_with_the_remedy(
        auth_client, session, deepgram, status, expected_status, code):
    _deepgram_provider(auth_client)
    deepgram.respond = lambda r: httpx.Response(status, json={"err_msg": "Bad audio"})
    res = _transcribe(auth_client)
    assert res.status_code == expected_status and res.json()["detail"]["code"] == code
    assert "dg-key-secret" not in res.text
    # Nothing was transcribed, so nothing is recorded.
    assert _events(session) == []


@pytest.mark.parametrize("error, phase, seconds", [
    (httpx.ReadTimeout, "waiting for its answer", "1800"),
    (httpx.WriteTimeout, "while we were sending the request", "30"),
    (httpx.ConnectTimeout, "while connecting", "10")])
def test_a_timeout_says_which_timer_fired_and_after_how_long(
        auth_client, session, deepgram, error, phase, seconds):
    """A hung vendor, a stalled upload and a dead network are different problems with
    different remedies; "did not respond in time" alone could not tell them apart."""
    _deepgram_provider(auth_client)

    def hang(request):
        raise error("timed out", request=request)

    deepgram.respond = hang
    res = _transcribe(auth_client)
    assert res.status_code == 504 and res.json()["detail"]["code"] == "upstream_timeout"
    message = res.json()["detail"]["message"]
    # Still opens as it always did, so nothing matching on it breaks.
    assert message.startswith("Deepgram did not respond in time")
    assert phase in message and f"after {seconds} s" in message
    assert _events(session) == []


def test_a_model_name_that_could_alter_the_request_is_refused(auth_client, deepgram):
    _deepgram_provider(auth_client)
    res = _transcribe(auth_client, model="nova-3&callback=https://evil.example")
    assert res.status_code == 400 and res.json()["detail"]["code"] == "bad_model"
    assert deepgram.calls == []


def test_empty_and_oversized_audio_are_refused_before_deepgram_is_asked(
        auth_client, monkeypatch, deepgram):
    _deepgram_provider(auth_client)
    assert _transcribe(auth_client, audio=b"").json()["detail"]["code"] == "empty_audio"
    monkeypatch.setattr(settings, "stt_max_upload_mb", 1)
    res = _transcribe(auth_client, audio=b"\x00" * (1024 * 1024 + 1))
    assert res.status_code == 413 and res.json()["detail"]["code"] == "audio_too_large"
    assert deepgram.calls == []


def test_an_app_transcribes_for_a_person_and_is_credited(
        client, auth_client, switch_user, admin, session, deepgram):
    switch_user(ADMIN_ID)
    secret = auth_client.post("/api/v1/apps/gam/secret").json()["data"]["secret"]
    switch_user(TEST_USER_ID)
    _deepgram_provider(auth_client)
    from app.auth import current_user
    from app.main import app as fastapi_app

    fastapi_app.dependency_overrides.pop(current_user, None)
    headers = {"X-Gecko-App": "gam", "X-Gecko-App-Secret": secret, "X-On-Behalf-Of": TEST_USER_ID,
               "content-type": "audio/flac"}
    # The readiness lookup is a GET: on the allowlist, or an app could never ask first.
    assert client.get("/api/v1/ai/speech/stt/ready", headers=headers).json()["data"]["ready"] is True
    res = client.post("/api/v1/ai/speech/stt", content=AUDIO, headers=headers)
    assert res.status_code == 200, res.text
    [event] = _events(session)
    assert (event.app, event.user_id) == ("gam", TEST_USER_ID)
