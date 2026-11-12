# Corpus

Download these two public documents from the FSSAI website (https://www.fssai.gov.in, Acts & Rules / Regulations)
and save them in this folder with the filenames in `corpus.yaml`:

1. Food Safety and Standards (Labelling and Display) Regulations, 2020 -> `labelling_and_display_2020.pdf`
2. Food Safety and Standards (Advertising and Claims) Regulations, 2018 -> `advertising_and_claims_2018.pdf`

Use the latest consolidated/amended copy and record the version and source URL in `corpus.yaml`.
Prefer PDFs with a text layer. If a PDF is scanned, run OCR first (for example `ocrmypdf in.pdf out.pdf`)
and save the OCR'd file here.

The PDFs are public regulatory documents and are meant to be committed so that a clean clone runs
with a single `docker compose up`.

## For Evaluation 1

Show which passages the "source of protein" case depends on:

```bash
docker compose run --rm indexer            # build the index
docker compose run --rm worker python - <<'PY'
from foodlabel.retrieval.search import get_retriever
r = get_retriever()
for doc in ("labelling", "claims"):
    for p in r.search("source of protein", doc=doc, k=3):
        print(doc, p["passage_id"], p["clause_ref"]); print(p["text"][:400]); print("---")
PY
```

Read the claims-regulation passage yourself, note the exact condition and threshold, and agree the
reference answer with your instructor. Then fill in `eval/eval_set.jsonl` (see `docs/evaluation.md`).
