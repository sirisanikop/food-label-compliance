# Retrieval and ingestion (L1, D2)

## The difficulty this must address (section 3)

Labelling requirements and claim conditions are in two different regulations, and the labelling regulation does not
point to the claim conditions. A single embedding search for "is 'source of protein' permitted" tends to return labelling
text. So retrieval is a **tool the agent calls repeatedly** (`search_corpus` with an optional `doc` filter), and the
verifier forces a claims-scoped search when a claim has no condition cited.

## Chunking (`retrieval/chunker.py`)

- Split on regulation / chapter / schedule headings: a clause is the unit of meaning, not a character window.
- A schedule's heading is repeated at the top of every chunk cut from it, so table rows stay tied to their heading.
- The text that is **embedded** is `doc title | section path | clause` + text, so terse table rows still match queries;
  the **stored** text is the raw passage (citations must be verbatim).
- Cross-references ("regulation 7", "Schedule II") are stored as `mentions` so the agent can follow them with `get_clause`.
- Each chunk carries `doc_role` (`labelling` / `claims`) from `data/corpus/corpus.yaml`; the agent can restrict a search to one.
- `passage_id = <doc>:<clause>:<hash of text>`; the verifier recomputes the hash, so a citation cannot resolve to altered text.

Tune the heading patterns against your real PDFs (`python -m pytest tests/llmops/test_chunker.py` shows the expected behaviour;
extract the PDF text and look at what the regexes actually match).

## Embeddings

Local sentence-transformers model named by `EMBED_MODEL` (no paid API), normalised, inner-product FAISS index via
LangChain. Query embeddings are cached in Redis.

## Pipeline (`python -m foodlabel.ingest.pipeline`, also the `indexer` service)

read `corpus.yaml` -> parse PDFs (pypdf) -> chunk -> embed (only chunks not already in the vector cache) -> build FAISS -> write
`manifest.json`. Idempotent: the build id hashes the document files, embedding model and chunker version; nothing changed means
nothing is touched. The index is written to a temp directory and swapped in.

**Re-embedding without rebuilding everything:** vectors are cached in `vectors.sqlite` keyed by (model, hash of embedded text).
`--reembed --doc <doc_id>` recomputes one document's vectors; changing `EMBED_MODEL` re-embeds all (different cache key) and the FAISS
index is rebuilt from cached vectors in seconds.

## Freshness (D2)

`manifest.json` records build time, document file hashes and versions, chunk count, and throughput (chunks/s, embedded vs cached).
Prometheus exposes `foodlabel_index_built_timestamp_seconds`, `foodlabel_index_chunks`, `foodlabel_index_documents`,
`foodlabel_ingest_throughput_chunks_per_second` from the serving process's own manifest.

## Not yet done (Person 3)

- Run the pipeline on the real PDFs and fix the heading regexes where the PDF text differs.
- Decide whether hybrid (keyword + vector) search helps for table rows; measure with context relevance before adding it.
- Re-ranking if precision is low.
- OCR step if any document is scanned.
