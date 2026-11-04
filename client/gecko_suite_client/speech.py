"""Speech through the suite: audio to text (Deepgram) now; text to audio next."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from gecko_suite_client.client import SuiteClient


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Segment:
    text: str
    start: float
    end: float
    speaker: Optional[int] = None
    words: list[Word] = field(default_factory=list)


@dataclass
class Transcript:
    segments: list[Segment]
    language: str
    model: str
    duration_seconds: float
    # What the suite recorded. None is unknown, never free.
    cost: Optional[float] = None
    usage_event_id: Optional[str] = None


@dataclass
class Spoken:
    audio: bytes
    media_type: str
    # What resolved. Send `model_ref` and `voice` back with every later chunk of the same
    # narration, so it cannot change voice partway through.
    provider: str
    model_ref: str
    voice: str
    cached: bool


class SuiteSpeech:
    def __init__(self, client: SuiteClient, *, app: Optional[str] = None):
        self.client = client
        self.app = app or client.app

    def transcribe_ready(self) -> bool:
        """Whether a transcription would find a Deepgram key. Ask before queueing work that
        is certain to fail; the answer is the suite's, not this app's."""
        return bool(self.client.request("GET", "/api/v1/ai/speech/stt/ready")["ready"])

    def transcribe(self, audio: bytes, *, content_type: str = "audio/flac", model: Optional[str] = None,
                   language: Optional[str] = None, subject_ref: Optional[str] = None) -> Transcript:
        """Transcribe `audio`, sent as the request body. Words, speakers and timings come back
        as the suite parsed them from Deepgram."""
        params = {k: v for k, v in (("model", model), ("language", language), ("app", self.app),
                                    ("subject_ref", subject_ref)) if v}
        data = self.client.request("POST", "/api/v1/ai/speech/stt", params=params, content=audio,
                                   headers={"Content-Type": content_type})
        return Transcript(
            segments=[Segment(text=s["text"], start=s["start"], end=s["end"], speaker=s.get("speaker"),
                              words=[Word(**w) for w in s.get("words", [])]) for s in data["segments"]],
            language=data["language"], model=data["model"], duration_seconds=data["duration_seconds"],
            cost=data.get("cost"), usage_event_id=data.get("usage_event_id"))

    def speak(self, text: str, *, voice: Optional[str] = None, model_ref: Optional[str] = None,
              subject_ref: Optional[str] = None) -> Spoken:
        """Read `text` aloud. With no `model_ref` the model `speech.tts` is routed to is used,
        and its default voice if none is named."""
        body = {k: v for k, v in (("text", text), ("voice", voice), ("model_ref", model_ref),
                                  ("app", self.app), ("subject_ref", subject_ref)) if v}
        response = self.client.request_raw("POST", "/api/v1/ai/speech/tts", json=body)
        return Spoken(
            audio=response.content, media_type=response.headers.get("content-type", "audio/mpeg"),
            provider=response.headers.get("x-speech-provider", ""),
            model_ref=response.headers.get("x-speech-model", ""),
            voice=response.headers.get("x-speech-voice", ""),
            cached=response.headers.get("x-speech-cached") == "true")
