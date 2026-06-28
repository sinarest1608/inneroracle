"""Self-contained BM25 sparse encoder — the keyword/lexical arm of hybrid search.

Produces Qdrant-compatible sparse vectors. Documents carry full BM25 term
weights; queries carry presence (1.0) for query terms, so a dot product equals
the standard BM25 score. Fitted vocab + IDF are persisted to JSON so the serving
side uses the same term space as indexing.

Pure Python, no heavy deps — chosen over FastEmbed/Qdrant-native BM25 so the
index path is testable without onnxruntime/torch. Swappable later.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

# Latin (incl. diacritics for IAST), Devanagari, and digits.
_TOKEN_RE = re.compile(r"[A-Za-zÀ-ɏऀ-ॿ]+|\d+")


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


@dataclass
class SparseVec:
    indices: list[int]
    values: list[float]


class BM25Encoder:
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.vocab: dict[str, int] = {}
        self.idf: dict[str, float] = {}
        self.avgdl: float = 0.0

    def fit(self, corpus: list[str]) -> "BM25Encoder":
        df: Counter[str] = Counter()
        total = 0
        for doc in corpus:
            toks = tokenize(doc)
            total += len(toks)
            for t in set(toks):
                df[t] += 1
        n = max(len(corpus), 1)
        self.avgdl = total / n
        for t, d in df.items():
            self.vocab[t] = len(self.vocab)
            self.idf[t] = math.log(1 + (n - d + 0.5) / (d + 0.5))
        return self

    def encode_document(self, text: str) -> SparseVec:
        toks = tokenize(text)
        dl = len(toks)
        tf = Counter(toks)
        idx: list[int] = []
        val: list[float] = []
        for t, f in tf.items():
            j = self.vocab.get(t)
            if j is None:
                continue
            denom = f + self.k1 * (1 - self.b + self.b * dl / max(self.avgdl, 1e-9))
            w = self.idf.get(t, 0.0) * (f * (self.k1 + 1)) / denom
            if w > 0:
                idx.append(j)
                val.append(w)
        return SparseVec(idx, val)

    def encode_query(self, text: str) -> SparseVec:
        idx: list[int] = []
        val: list[float] = []
        for t in set(tokenize(text)):
            j = self.vocab.get(t)
            if j is not None:
                idx.append(j)
                val.append(1.0)
        return SparseVec(idx, val)

    def save(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            json.dumps(
                {"k1": self.k1, "b": self.b, "avgdl": self.avgdl,
                 "vocab": self.vocab, "idf": self.idf}
            )
        )

    @classmethod
    def load(cls, path: str) -> "BM25Encoder":
        data = json.loads(Path(path).read_text())
        enc = cls(k1=data["k1"], b=data["b"])
        enc.avgdl = data["avgdl"]
        enc.vocab = data["vocab"]
        enc.idf = data["idf"]
        return enc
