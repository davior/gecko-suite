"""Embeddings through the suite, in the shape GAM's embedder already has."""

from __future__ import annotations

from typing import Optional, Sequence

from gecko_suite_client.client import SuiteClient

# The suite caps one request; larger inputs are split here so callers need not know.
MAX_TEXTS_PER_CALL = 2048


class SuiteEmbedder:
    """`embed(texts)` returns vectors in order; `model` and `dimensions` say what made them.

    Store `model` beside every vector. Vectors from two models are not comparable, and a
    routing change in the suite must never silently mix them in one index.
    """

    def __init__(self, client: SuiteClient, *, app: Optional[str] = None, dimensions: Optional[int] = None,
                 model_ref: Optional[str] = None, subject_ref: Optional[str] = None):
        self.client, self.app, self.dimensions_requested = client, app or client.app, dimensions
        self.model_ref, self.subject_ref = model_ref, subject_ref
        self.model = ""
        self.dimensions = dimensions or 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), MAX_TEXTS_PER_CALL):
            body: dict = {"texts": list(texts[start:start + MAX_TEXTS_PER_CALL])}
            for key, value in (("app", self.app), ("dimensions", self.dimensions_requested),
                               ("model_ref", self.model_ref), ("subject_ref", self.subject_ref)):
                if value:
                    body[key] = value
            data = self.client.request("POST", "/api/v1/ai/embed", json=body)
            self.model, self.dimensions = data["model"], data["dimensions"]
            vectors.extend(data["vectors"])
        return vectors
