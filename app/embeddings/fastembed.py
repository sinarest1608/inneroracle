"""BGE dense embedder via fastembed (ONNX). No torch — light + fast cold start.

Uses `BAAI/bge-large-en-v1.5` (1024-d, cosine) on onnxruntime. fastembed's 0.8
catalog doesn't expose the multilingual bge-m3 dense vectors, so we use the same
BGE family's strong English model — the corpus (English translation + commentary +
romanized transliteration) and the life-question queries are English-dominant.
fastembed applies BGE's query instruction automatically in `query_embed`. The index
is (re)built with this same backend, so query and document vectors share one space.
"""

from __future__ import annotations


class FastEmbedEmbedder:
    dim = 1024

    def __init__(self, model_name: str = "BAAI/bge-large-en-v1.5"):
        self._model_name = model_name
        self._model = None

    def _load(self):
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(self._model_name)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._load().embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return next(self._load().query_embed(text)).tolist()
