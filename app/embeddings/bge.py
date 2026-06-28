"""BGE-M3 dense embedder (local). FlagEmbedding/torch imported lazily."""

from __future__ import annotations


class BGEM3Embedder:
    dim = 1024

    def __init__(self, model_name: str = "BAAI/bge-m3", use_fp16: bool = False):
        self._model_name = model_name
        self._use_fp16 = use_fp16
        self._model = None

    def _load(self):
        if self._model is None:
            from FlagEmbedding import BGEM3FlagModel

            self._model = BGEM3FlagModel(self._model_name, use_fp16=self._use_fp16)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        vecs = self._load().encode(
            texts, return_dense=True, return_sparse=False, return_colbert_vecs=False
        )["dense_vecs"]
        return [v.tolist() for v in vecs]

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]
