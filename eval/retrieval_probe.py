"""Retrieval probe for the claims regulation: where does the right schedule row rank under different
retrieval variants?  Run inside the worker container (it has the index and the embedding model):

    docker compose run --rm -T worker python - < eval/retrieval_probe.py

Variants (all over the 'claims' passages only, so the numbers are comparable):
  baseline  the vectors that are in the index right now (dense, current embed text)
  text-only dense, but embed just the stored passage text (no 'doc | path | clause' prefix)
  row-first dense, embedding "<row title>: <row text>" for schedule rows (drops the repeated heading)
  bm25      lexical BM25 over passage text + section path
  hybrid    reciprocal-rank fusion (k=60) of baseline + bm25
  hybrid2   reciprocal-rank fusion of row-first + bm25
"""
import math
import re
from collections import Counter

import numpy as np

from foodlabel.retrieval.search import get_retriever

# (query, substring that identifies the correct passage in section_path)
CASES = [
    ("source of protein", "Row 11: Protein"),
    ("high protein claim", "Row 11: Protein"),
    ("what must a product contain to claim it is a source of protein", "Row 11: Protein"),
    ("source of dietary fibre", "Row 14: Dietary fibre"),
    ("low fat claim", "Row 2: Fat"),
    ("sodium free", "Row 13: Sodium"),
    ("sugar free claim", "Row 10: Sugars"),
    ("gluten free claim", "Row 19: Gluten"),
    ("trans fat free", "Row 6: Trans fat"),
]


def tok(s):
    return re.findall(r"[a-z0-9]+", s.lower())


class BM25:
    def __init__(self, texts, k1=1.5, b=0.75):
        self.docs = [tok(t) for t in texts]
        self.k1, self.b = k1, b
        self.avg = sum(len(d) for d in self.docs) / len(self.docs)
        df = Counter(w for d in self.docs for w in set(d))
        n = len(self.docs)
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}
        self.tf = [Counter(d) for d in self.docs]

    def scores(self, query):
        q = tok(query)
        out = []
        for d, tf in zip(self.docs, self.tf):
            s = 0.0
            for w in q:
                if w in tf:
                    f = tf[w]
                    s += self.idf[w] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(d) / self.avg))
            out.append(s)
        return np.array(out)


def ranks_of(scores):
    return list(np.argsort(-scores))


def rrf(*score_arrays, k=60):
    fused = np.zeros(len(score_arrays[0]))
    for sc in score_arrays:
        for rank, idx in enumerate(ranks_of(sc), start=1):
            fused[idx] += 1.0 / (k + rank)
    return fused


def row_first_text(d):
    sp = d.metadata.get("section_path", "")
    if "> Row " not in sp:
        return d.page_content
    title = sp.split("> Row ", 1)[1]  # "11: Protein"
    lines = d.page_content.splitlines()[1:]  # drop the repeated schedule heading
    return f"{title}\n" + "\n".join(lines)


def main():
    r = get_retriever()
    store = r.store
    items = []  # (doc, baseline vector)
    for i, docid in store.index_to_docstore_id.items():
        d = store.docstore._dict[docid]
        if d.metadata["doc_role"] == "claims":
            items.append((d, np.array(store.index.reconstruct(int(i)), dtype="float32")))
    docs = [d for d, _ in items]
    base = np.vstack([v for _, v in items])
    print(f"claims passages: {len(docs)}; build_id: {r.build_id}")

    emb = r.embeddings
    text_only = np.array(emb.embed_documents([d.page_content for d in docs]), dtype="float32")
    row_first = np.array(emb.embed_documents([row_first_text(d) for d in docs]), dtype="float32")
    bm = BM25([d.page_content + " " + d.metadata.get("section_path", "") for d in docs])

    def gold_idx(sub):
        return [i for i, d in enumerate(docs) if sub in d.metadata.get("section_path", "")]

    results = {name: [] for name in ("baseline", "text-only", "row-first", "bm25", "hybrid", "hybrid2")}
    sanity = []
    for q, sub in CASES:
        qv = np.array(emb.embed_query(q), dtype="float32")
        sc = {
            "baseline": base @ qv, "text-only": text_only @ qv, "row-first": row_first @ qv, "bm25": bm.scores(q),
        }
        sc["hybrid"] = rrf(sc["baseline"], sc["bm25"])
        sc["hybrid2"] = rrf(sc["row-first"], sc["bm25"])
        gold = gold_idx(sub)
        for name, s in sc.items():
            order = ranks_of(s)
            rank = min((order.index(g) + 1 for g in gold), default=None)
            results[name].append(rank)
        top = ranks_of(sc["baseline"])[0]
        live = r.search(q, doc="claims", k=1)[0]["passage_id"]
        sanity.append(docs[top].metadata["passage_id"] == live)

    print("\nrank of the correct row per query (1 = best; None = row not found):")
    print(f"{'query':64s} " + " ".join(f"{n:>9s}" for n in results))
    for j, (q, sub) in enumerate(CASES):
        print(f"{q[:64]:64s} " + " ".join(f"{str(results[n][j]):>9s}" for n in results))
    print("\nsummary            " + " ".join(f"{n:>9s}" for n in results))
    for label, fn in (
        ("hit@1", lambda rk: rk == 1),
        ("hit@3", lambda rk: rk is not None and rk <= 3),
        ("MRR", lambda rk: 0 if rk is None else 1.0 / rk),
    ):
        print(f"{label:18s} " + " ".join(f"{sum(fn(rk) for rk in results[n]) / len(CASES):9.2f}" for n in results))
    print("\nmanual baseline top-1 matches live search() top-1 for every query:", all(sanity))

    print("\nwhat the live search returns for the first query (top 5, claims):")
    for p in r.search(CASES[0][0], doc="claims", k=5):
        print(f"  {p['score']:.3f}  {p['clause_ref']:12s} {p['section_path'].split(' > ')[-1][:50]}")


main()
