"""Embedding models (no paid API). The model name comes from EMBED_MODEL.

  * a sentence-transformers model name (e.g. sentence-transformers/all-MiniLM-L6-v2): the real setting.
  * "local-hash": an offline, dependency-free lexical embedding (hashed word and bigram features).
    Needs no download and no torch, so tests, CI and a machine without internet can build and query an
    index. Retrieval quality is lower than a trained model: use it to develop and demo wiring, and report
    results with a real model.
"""
from __future__ import annotations

import hashlib
import math
import re
from functools import lru_cache

from foodlabel.common.config import get_settings

LOCAL_HASH = "local-hash"
_DIM = 512
_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
_STOP = frozenset("the of and or a an to in for is are be by with as on at that this it its from shall may any such".split())


def _bucket(feature: str) -> tuple[int, float]:
    h = hashlib.blake2b(feature.encode(), digest_size=8).digest()
    n = int.from_bytes(h, "big")
    return n % _DIM, 1.0 if (n >> 63) & 1 else -1.0


def _embed(text: str) -> list[float]:
    toks = [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]
    feats: dict[str, float] = {}
    for t in toks:
        feats[t] = feats.get(t, 0.0) + 1.0
    for a, b in zip(toks, toks[1:]):
        feats[a + "_" + b] = feats.get(a + "_" + b, 0.0) + 1.5  # phrases ("source of protein") matter
    vec = [0.0] * _DIM
    for f, c in feats.items():
        i, sign = _bucket(f)
        vec[i] += sign * (1.0 + math.log(c))
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class HashEmbeddings:
    """Minimal LangChain-compatible Embeddings implementation."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [_embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return _embed(text)

    async def aembed_documents(self, texts):  # pragma: no cover - interface completeness
        return self.embed_documents(texts)

    async def aembed_query(self, text):  # pragma: no cover
        return self.embed_query(text)

    def __call__(self, text: str) -> list[float]:  # FAISS docstore fallback expects a callable
        return self.embed_query(text)


@lru_cache(maxsize=2)
def get_embeddings(model_name: str | None = None):
    name = model_name or get_settings().embed_model
    if not name:
        get_settings().require("embed_model")
    if name == LOCAL_HASH:
        return HashEmbeddings()
    from langchain_huggingface import HuggingFaceEmbeddings

    return HuggingFaceEmbeddings(model_name=name, encode_kwargs={"normalize_embeddings": True})
