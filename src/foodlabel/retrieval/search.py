"""Retriever used by the agent's tools (L1). LangChain + FAISS, local embeddings.

search(query, doc=None, k=5)  - semantic search, optionally restricted to one regulation role
get_clause(clause_id)         - exact passage id, or every passage whose clause_ref matches

Query embeddings and whole retrieval results are cached (D7). The retrieval cache key includes
the index build id, so rebuilding the index invalidates it automatically.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

from foodlabel.common.config import get_settings
from foodlabel.perf import cache

log = logging.getLogger("foodlabel.retrieval")


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
        key = cache.digest(query, doc, k, self.build_id)
        hit = cache.get_json("retrieval", key)
        if hit is not None:
            return hit
        vec = self._embed_query(query)
        flt = {"doc_role": doc} if doc else None
        hits = self.store.similarity_search_with_score_by_vector(vec, k=k, filter=flt, fetch_k=max(60, k * 12))
        out = [self._to_passage(d, sc) for d, sc in hits]
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
