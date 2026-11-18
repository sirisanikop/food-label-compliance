"""Retriever used by the agent's tools (L1). LangChain + FAISS, local embeddings.

search(query, doc=None, k=5)  - hybrid search (vectors + BM25, score-blended), optionally restricted to one regulation role
get_clause(clause_id)         - exact passage id, or every passage whose clause_ref matches

Query embeddings and whole retrieval results are cached (D7). The retrieval cache key includes
the index build id, so rebuilding the index invalidates it automatically.
"""
from __future__ import annotations

import logging
import math
import os
import re
import threading
from collections import Counter
from typing import Optional

from foodlabel.common.config import get_settings
from foodlabel.perf import cache

log = logging.getLogger("foodlabel.retrieval")

_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
_STOP = frozenset("the of and or a an to in for is are be by with as on at that this it its from shall may any such per".split())


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


class _BM25:
    """Small in-memory BM25 over the (few hundred) chunks. Complements vectors for exact terms
    ("source of protein", "Schedule I", "11. Protein") that embeddings blur."""

    def __init__(self, docs: list[tuple[str, str]], k1: float = 1.4, b: float = 0.75):
        self.k1, self.b = k1, b
        self.ids = [d[0] for d in docs]
        self.tf = [Counter(_tokens(d[1])) for d in docs]
        self.len = [sum(c.values()) or 1 for c in self.tf]
        self.avg = sum(self.len) / len(self.len)
        df = Counter(t for c in self.tf for t in c)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def score(self, query: str) -> dict[str, float]:
        q = _tokens(query)
        grams = [a + " " + b for a, b in zip(q, q[1:])]
        out: dict[str, float] = {}
        for i, tf in enumerate(self.tf):
            s = 0.0
            for t in q:
                f = tf.get(t, 0)
                if f:
                    s += self.idf.get(t, 0.0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
            if s:
                out[self.ids[i]] = s
        return out


class IndexNotReady(RuntimeError):
    pass


class Retriever:
    def __init__(self, index_dir: Optional[str] = None, embed_model: Optional[str] = None):
        from langchain_community.vectorstores import FAISS

        from foodlabel.ingest.freshness import read_manifest
        from foodlabel.retrieval.embedder import get_embeddings

        s = get_settings()
        self.index_dir = index_dir or s.index_dir
        self.manifest = read_manifest(self.index_dir)
        if not self.manifest:
            raise IndexNotReady(f"no index manifest in {self.index_dir}; run the indexer")
        self.model = embed_model or self.manifest["embed_model"]
        self.embeddings = get_embeddings(self.model)
        # The pickle is produced by our own indexer on a private volume.
        self.store = FAISS.load_local(
            os.path.join(self.index_dir, "faiss"), self.embeddings, allow_dangerous_deserialization=True
        )
        self.build_id = self.manifest["build_id"]
        self._docs = {d.metadata["passage_id"]: d for d in self.store.docstore._dict.values()}
        self._bm25 = _BM25([(pid, d.page_content + " " + d.metadata.get("section_path", "") + " " + d.metadata.get("clause_ref", ""))
                            for pid, d in self._docs.items()])

    # ------------------------------------------------------------------ helpers
    def _embed_query(self, query: str) -> list[float]:
        key = cache.digest(self.model, query)
        hit = cache.get_json("qemb", key)
        if hit:
            return hit
        vec = self.embeddings.embed_query(query)
        cache.set_json("qemb", key, vec, ttl=24 * 3600)
        return vec

    @staticmethod
    def _to_passage(doc, score: Optional[float]) -> dict:
        md = doc.metadata
        return {
            "passage_id": md["passage_id"], "doc_id": md["doc_id"], "doc_role": md["doc_role"],
            "clause_ref": md["clause_ref"], "section_path": md.get("section_path", ""),
            "page": md.get("page"), "mentions": md.get("mentions", []),
            "text": doc.page_content, "score": None if score is None else float(score),
        }

    # --------------------------------------------------------------------- api
    def search(self, query: str, doc: Optional[str] = None, k: int = 5) -> list[dict]:
        key = cache.digest(query, doc, k, self.build_id, "hybrid-mm-1", os.environ.get("RETRIEVAL_LEXICAL_WEIGHT", "0.65"))  # algorithm tag: changing ranking invalidates cached results
        hit = cache.get_json("retrieval", key)
        if hit is not None:
            return hit
        vec = self._embed_query(query)
        flt = {"doc_role": doc} if doc else None
        pool = max(60, k * 12)
        vec_hits = self.store.similarity_search_with_score_by_vector(vec, k=pool, filter=flt, fetch_k=pool * 4)
        vec_score = {d.metadata["passage_id"]: sc for d, sc in vec_hits}
        lex = {pid: sc for pid, sc in self._bm25.score(query).items()
               if not doc or self._docs[pid].metadata["doc_role"] == doc}
        # Min-max normalise each signal to [0, 1] and blend. Measured on the verified eval scenarios this
        # beat rank fusion (top-5 recall 0.94 vs 0.47 with the offline embedder), because exact terms such
        # as "protein" or "composite foods" are decisive here and rank fusion rewards agreement over strength.
        w = float(os.environ.get("RETRIEVAL_LEXICAL_WEIGHT", "0.65"))
        vmin, vmax = (min(vec_score.values()), max(vec_score.values())) if vec_score else (0.0, 1.0)
        lmax = max(lex.values()) if lex else 1.0
        fused = {pid: (1 - w) * ((vec_score.get(pid, vmin) - vmin) / ((vmax - vmin) or 1.0)) + w * (lex.get(pid, 0.0) / lmax)
                 for pid in set(vec_score) | set(lex)}
        top = sorted(fused.items(), key=lambda kv: -kv[1])[:k]
        out = [self._to_passage(self._docs[pid], sc) for pid, sc in top]
        cache.set_json("retrieval", key, out)
        return out

    def get_clause(self, clause_id: str) -> list[dict]:
        cid = clause_id.strip().lower()
        out = []
        for d in self.store.docstore._dict.values():  # small corpus: a scan is fine
            md = d.metadata
            if md["passage_id"].lower() == cid or md["clause_ref"].lower() == cid or md["clause_ref"].lower().replace(" ", "-") == cid:
                out.append(self._to_passage(d, None))
        return out[:6]


_lock = threading.Lock()
_retriever: Optional[Retriever] = None


def get_retriever() -> Retriever:
    global _retriever
    with _lock:
        if _retriever is None:
            _retriever = Retriever()
        return _retriever
