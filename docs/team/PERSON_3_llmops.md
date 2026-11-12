# Person 3 - Retrieval and LLMOps

**Requirements:** L1, L2, L3, L4, L5, L6, D2. Read `docs/retrieval.md`, `docs/evaluation.md`, `docs/guardrails.md`.

## Week-1 priority

Get the two FSSAI PDFs into `data/corpus/`, fill `corpus.yaml`, and make `docker compose run --rm indexer` build an index.
Look at what the chunker actually produces for these PDFs and fix the heading patterns; nothing else works well on bad chunks.
Then show the team the passages the "source of protein" case depends on (this is the Evaluation 1 deliverable).

## Then

1. Fill in `expected_route` / `expected_passages` in `eval/eval_set.jsonl` from the real regulation text; set `verified_against_corpus`.
2. Run baseline, then `analyst=v2`, report the comparison (L2). Make one deliberate change and report before/after (L3).
3. Red-team the guardrails with your own inputs and add each as a test (L4).
4. Run `python -m foodlabel.llmops.costs --last 100` after a batch of real runs: cost per 100 runs and largest driver (L5).
5. Make sure re-embedding one document works (`--reembed --doc`) and measure ingestion throughput (D2).

## Files

`src/foodlabel/retrieval/`, `ingest/`, `llmops/`, `evaluation/`, `services/indexer/`, `prompts/`, `eval/`, `data/corpus/`,
`docs/retrieval.md`, `evaluation.md`, `guardrails.md`, `tests/llmops/`.

## Note

The retrieval and ingestion code (`retrieval/search.py`, `ingest/pipeline.py`) needs the embedding model and FAISS and could not be run
in the environment where this scaffold was built. The chunker, guardrails, metrics, prompts, costs and feedback are covered by tests.
Expect to debug the first real index build.
