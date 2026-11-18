"""Builds a real FAISS index from a tiny corpus with the offline embedder and queries it."""
import textwrap

import pytest

pytest.importorskip("faiss")
pytest.importorskip("langchain_community")

from foodlabel.common import config  # noqa: E402
from foodlabel.ingest import pipeline  # noqa: E402
from foodlabel.perf import cache  # noqa: E402

LAB = """CHAPTER II
5. Labelling of pre-packaged foods
(1) Every package shall carry the name of the food and the net quantity of the contents.
(2) The allergens present shall be declared in a statement beginning with the word Contains.
"""
CLAIMS = """SCHEDULE I
Nutrient Content Claim
2. Fat Low Not more than 3 g of fat per 100 g for solids.
11. Protein Source 10 per cent. of Recommended Dietary Allowance per 100 g for solids
14. Dietary fibre Source Product contains at least 3 g of fibre per 100 g for solids.
"""


@pytest.fixture()
def retriever(tmp_path, monkeypatch):
    corpus, index = tmp_path / "corpus", tmp_path / "index"
    corpus.mkdir()
    (corpus / "lab.txt").write_text(LAB)
    (corpus / "claims.txt").write_text(CLAIMS)
    (corpus / "corpus.yaml").write_text(textwrap.dedent("""
        documents:
          - {doc_id: LAB, title: Labelling Regs, role: labelling, file: lab.txt}
          - {doc_id: ADV, title: Claims Regs, role: claims, file: claims.txt}
    """))
    monkeypatch.setenv("EMBED_MODEL", "local-hash")
    monkeypatch.setenv("CORPUS_DIR", str(corpus))
    monkeypatch.setenv("INDEX_DIR", str(index))
    config.reload_settings()
    out = pipeline.run()
    from foodlabel.retrieval.search import Retriever

    yield Retriever(), out, pipeline
    config.reload_settings()


def test_ingestion_is_idempotent_and_records_freshness(retriever):
    r, out, pl = retriever
    assert out["status"] == "built" and out["chunk_count"] >= 4 and out["throughput"]["total_seconds"] >= 0
    assert pl.run()["status"] == "up_to_date"  # nothing changed -> nothing rebuilt
    again = pl.run(reembed=True)
    assert again["status"] == "built" and again["throughput"]["embedded_chunks"] == again["chunk_count"]


def test_exact_terms_are_found_and_roles_filter(retriever):
    r, *_ = retriever
    top = r.search("source of protein", doc="claims", k=2)
    assert "11. Protein" in top[0]["text"] and top[0]["doc_role"] == "claims"
    assert all(p["doc_role"] == "labelling" for p in r.search("allergen statement", doc="labelling", k=3))
    assert r.search("net quantity", doc=None, k=1)[0]["doc_id"] == "LAB"


def test_get_clause_and_cache_key_tracks_build(retriever):
    r, *_ = retriever
    pid = r.search("fibre", doc="claims", k=1)[0]["passage_id"]
    assert r.get_clause(pid)[0]["passage_id"] == pid
    r.search("fibre", doc="claims", k=1)
    assert cache.get_json("retrieval", cache.digest("fibre", "claims", 1, r.build_id, "hybrid-mm-1", "0.65")) is not None
