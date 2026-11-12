"""Idempotent ingestion pipeline: read -> parse -> chunk -> embed -> index (D2).

    python -m foodlabel.ingest.pipeline                 # build / refresh
    python -m foodlabel.ingest.pipeline --reembed       # re-embed everything (e.g. new model)
    python -m foodlabel.ingest.pipeline --reembed --doc FSSAI-ADV-CLAIMS-2018   # one document only

Idempotent: the build id is a hash of (document file hashes, embedding model, chunker version).
If nothing changed the run exits without touching the index. Vectors are cached in sqlite keyed by
(model, hash of embedded text), so re-embedding one document does not re-embed the others and the
FAISS index is rebuilt from cached vectors in seconds ("re-embed without rebuilding everything").
The new index is written to a temp dir and swapped in, so readers never see a half-built index.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import sys
import time
from typing import Optional

import yaml

from foodlabel.common.config import get_settings
from foodlabel.retrieval import chunker

CHUNKER_VERSION = "3"  # 2: heading detection + schedule-row chunking for the real FSSAI PDFs; 3: row-first embed text
log = logging.getLogger("foodlabel.ingest")


def load_catalog(corpus_dir: str) -> list[dict]:
    path = os.path.join(corpus_dir, "corpus.yaml")
    with open(path, encoding="utf-8") as fh:
        cat = yaml.safe_load(fh)
    return cat["documents"]


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_pages(path: str) -> list[str]:
    """Text per page. Use PDFs with a text layer; scanned PDFs need OCR first (see data/corpus/README.md)."""
    if path.lower().endswith((".txt", ".md")):
        with open(path, encoding="utf-8") as fh:
            return [fh.read()]
    from pypdf import PdfReader

    return [(p.extract_text() or "") for p in PdfReader(path).pages]


class VectorCache:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS vec (model TEXT, h TEXT, dim INT, v BLOB, PRIMARY KEY (model, h))")

    @staticmethod
    def key(text: str) -> str:
        return hashlib.sha1(text.encode("utf-8")).hexdigest()

    def get(self, model: str, h: str):
        import numpy as np

        row = self.db.execute("SELECT v FROM vec WHERE model=? AND h=?", (model, h)).fetchone()
        return np.frombuffer(row[0], dtype="float32") if row else None

    def put(self, model: str, h: str, vec) -> None:
        import numpy as np

        arr = np.asarray(vec, dtype="float32")
        self.db.execute("INSERT OR REPLACE INTO vec VALUES (?,?,?,?)", (model, h, int(arr.shape[0]), arr.tobytes()))

    def delete(self, model: str, hashes: list[str]) -> None:
        self.db.executemany("DELETE FROM vec WHERE model=? AND h=?", [(model, h) for h in hashes])

    def commit(self) -> None:
        self.db.commit()


def run(*, reembed: bool = False, only_doc: Optional[str] = None, corpus_dir: Optional[str] = None, index_dir: Optional[str] = None) -> dict:
    s = get_settings()
    s.require("embed_model")
    corpus_dir, index_dir = corpus_dir or s.corpus_dir, index_dir or s.index_dir
    t0 = time.perf_counter()
    docs = load_catalog(corpus_dir)
    if not docs:
        raise SystemExit("corpus.yaml lists no documents")

    infos = []
    for d in docs:
        path = os.path.join(corpus_dir, d["file"])
        if not os.path.exists(path):
            raise SystemExit(f"missing corpus file: {path} (see data/corpus/README.md)")
        infos.append({**d, "path": path, "sha256": sha256_file(path)})

    build_id = hashlib.sha1(
        json.dumps([[i["doc_id"], i["sha256"]] for i in infos] + [s.embed_model, CHUNKER_VERSION], sort_keys=True).encode()
    ).hexdigest()[:16]
    manifest_file = os.path.join(index_dir, "manifest.json")
    if os.path.exists(manifest_file) and not reembed:
        with open(manifest_file, encoding="utf-8") as fh:
            if json.load(fh).get("build_id") == build_id:
                log.info("index up to date", extra={"build_id": build_id})
                return {"status": "up_to_date", "build_id": build_id}

    from langchain_community.vectorstores import FAISS
    from langchain_community.vectorstores.utils import DistanceStrategy
    from langchain_core.documents import Document

    from foodlabel.retrieval.embedder import get_embeddings

    os.makedirs(index_dir, exist_ok=True)
    cache = VectorCache(os.path.join(index_dir, "vectors.sqlite"))
    emb = get_embeddings(s.embed_model)

    all_chunks: list[chunker.Chunk] = []
    doc_stats = []
    for i in infos:
        pages = read_pages(i["path"])
        chunks = chunker.chunk_document(pages, doc_id=i["doc_id"], doc_role=i["role"], doc_title=i["title"])
        all_chunks += chunks
        doc_stats.append({"doc_id": i["doc_id"], "role": i["role"], "file": i["file"], "sha256": i["sha256"],
                          "version": i.get("version", ""), "source_url": i.get("source_url", ""),
                          "pages": len(pages), "chunks": len(chunks)})
        log.info("chunked", extra={"doc": i["doc_id"], "chunks": len(chunks)})

    if reembed:  # drop cached vectors for the targeted documents (or all) so they are recomputed
        targets = [c for c in all_chunks if not only_doc or c.doc_id == only_doc]
        cache.delete(s.embed_model, [cache.key(c.embed_text) for c in targets])
        cache.commit()

    vectors, to_embed = {}, []
    for c in all_chunks:
        h = cache.key(c.embed_text)
        v = cache.get(s.embed_model, h)
        if v is None:
            to_embed.append((h, c))
        else:
            vectors[h] = v
    t_embed = time.perf_counter()
    for start in range(0, len(to_embed), 64):
        batch = to_embed[start : start + 64]
        for (h, _c), v in zip(batch, emb.embed_documents([c.embed_text for _h, c in batch])):
            cache.put(s.embed_model, h, v)
            vectors[h] = cache.get(s.embed_model, h)
        cache.commit()
    embed_s = time.perf_counter() - t_embed

    pairs = [(c.text, vectors[cache.key(c.embed_text)]) for c in all_chunks]
    metas = [{"passage_id": c.passage_id, "doc_id": c.doc_id, "doc_role": c.doc_role, "clause_ref": c.clause_ref,
              "section_path": c.section_path, "page": c.page, "mentions": c.mentions} for c in all_chunks]
    store = FAISS.from_embeddings(pairs, emb, metadatas=metas, distance_strategy=DistanceStrategy.MAX_INNER_PRODUCT,
                                  normalize_L2=False)
    tmp = os.path.join(index_dir, "faiss.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    store.save_local(tmp)
    final = os.path.join(index_dir, "faiss")
    shutil.rmtree(final, ignore_errors=True)
    os.replace(tmp, final)

    total_s = time.perf_counter() - t0
    manifest = {
        "build_id": build_id, "built_at": time.time(), "embed_model": s.embed_model, "chunker_version": CHUNKER_VERSION,
        "chunk_count": len(all_chunks), "documents": doc_stats,
        "throughput": {"chunks_per_second": round(len(all_chunks) / total_s, 2) if total_s else 0,
                       "embedded_chunks": len(to_embed), "cached_chunks": len(all_chunks) - len(to_embed),
                       "embed_seconds": round(embed_s, 2), "total_seconds": round(total_s, 2)},
    }
    with open(manifest_file + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    os.replace(manifest_file + ".tmp", manifest_file)
    log.info("index built", extra=manifest["throughput"])
    return {"status": "built", **manifest}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Build the corpus index")
    ap.add_argument("--reembed", action="store_true", help="recompute embeddings (all docs, or --doc)")
    ap.add_argument("--doc", help="with --reembed: only this doc_id")
    a = ap.parse_args()
    out = run(reembed=a.reembed, only_doc=a.doc)
    print(json.dumps({k: out[k] for k in ("status", "build_id", "chunk_count", "throughput") if k in out}, indent=2))


if __name__ == "__main__":
    sys.exit(main())
