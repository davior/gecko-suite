"""Embeddings through the suite, in the shape GAM's embedder already has."""

from __future__ import annotations

from typing import Optional, Sequence

from gecko_suite_client.client import SuiteClient

# The suite caps one request; larger inputs are split here so callers need not know.
MAX_TEXTS_PER_CALL = 2048


class SuiteEmbedder:
    """`embed(texts)` returns vectors in order; `model` and `dimensions` say what makes them.

    Store `model` beside every vector. Vectors from two models are not comparable, and a
    routing change in the suite must never silently mix them in one index.

    `model` and `dimensions` are known *before* the first `embed()`, by asking the suite
    which model the task routes to. GAM reads them to decide which stored vectors a query
    may be compared with, and to report coverage, neither of which embeds anything.
    """

    def __init__(self, client: SuiteClient, *, app: Optional[str] = None, dimensions: Optional[int] = None,
                 model_ref: Optional[str] = None, subject_ref: Optional[str] = None,
                 task: str = "embed.text"):
        self.client, self.app, self.dimensions_requested = client, app or client.app, dimensions
        self.model_ref, self.subject_ref, self.task = model_ref, subject_ref, task
        self._model = ""
        self._dimensions = dimensions or 0
        self._resolved = False

    def resolve(self) -> Optional[dict]:
        """The model this embedder would use, or None when nothing is routed for it.

        Asked of the suite, so it answers "is semantic search set up" without spending
        anything. Raises SuiteUnavailable when the suite cannot be reached.
        """
        if self.model_ref:
            found = next((m for m in self.client.request("GET", "/api/v1/models", params={"capability": "embed"})
                          if m["id"] == self.model_ref), None)
        else:
            query = {"task": self.task, **({"app": self.app} if self.app else {})}
            found = self.client.request("GET", "/api/v1/resolve", params=query).get("model")
        if found:
            self._model = found["model_id"]
            # What was asked for wins: a model trained to be truncated (OpenAI's) is asked
            # for fewer dimensions than it natively has. Otherwise the model's own size.
            self._dimensions = self.dimensions_requested or found.get("embed_dims") or self._dimensions
        self._resolved = True
        return found

    @property
    def model(self) -> str:
        if not self._model and not self._resolved:
            self.resolve()
        return self._model

    @property
    def dimensions(self) -> int:
        if not self._dimensions and not self._resolved:
            self.resolve()
        return self._dimensions

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), MAX_TEXTS_PER_CALL):
            body: dict = {"texts": list(texts[start:start + MAX_TEXTS_PER_CALL])}
            for key, value in (("app", self.app), ("dimensions", self.dimensions_requested),
                               ("model_ref", self.model_ref), ("subject_ref", self.subject_ref)):
                if value:
                    body[key] = value
            data = self.client.request("POST", "/api/v1/ai/embed", json=body)
            # What actually answered is authoritative over what was routed a moment ago.
            self._model, self._dimensions, self._resolved = data["model"], data["dimensions"], True
            vectors.extend(data["vectors"])
        return vectors
