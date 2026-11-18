# Packaged Food Label Compliance Check Service

A multi-agent service that takes a proposed packaged-food label and package specification, finds which
FSSAI labelling and claims requirements are met or unmet, and routes it to `clear-for-print`,
`revise-and-recheck` or `regulatory-review` with citations. **It never approves a label**: a regulatory
affairs professional decides.

Course: UE24AM342AA5 (ML System Design and AgentOps), Mini Project 10.

```
 web (nginx) --> api (FastAPI) --> redis queue --> worker x N (analyst agent, LangGraph)
                    |                                  |  tools: search_corpus / get_clause /
                    |                                  |         get_request_field / compute
                    |                                  +--> verifier (separate agent service)
                    |                                  +--> llm provider (or llm-stub)
                    +-- quarantine, rate limit         +--> FAISS index (built by indexer)
 prometheus / grafana / jaeger / cadvisor observe everything
```

## Run it (clean clone)

Requirements: Docker with Compose v2.24 or newer.

1. **Add the corpus.** Download the two FSSAI regulations into `data/corpus/` (names and sources in
   `data/corpus/README.md`) and fill in the version/URL fields in `data/corpus/corpus.yaml`.
2. **Start everything:**
   ```bash
   docker compose up --build
   ```
   The first start downloads the embedding model and builds the index; later starts are fast.
   Configuration comes from `.env.example`; copy it to `.env` to override anything. By default the model
   is the local `llm-stub` service, so it runs at zero cost. To use a real provider's free tier, set
   `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` in `.env` (any OpenAI-compatible endpoint).
3. **Use it:**

| What | Where |
|---|---|
| Web client | http://localhost:8080 |
| API docs | http://localhost:8000/docs |
| Grafana (dashboard "Food Label Compliance - Service") | http://localhost:3000 |
| Jaeger traces | http://localhost:16686 |
| Prometheus | http://localhost:9090 |

**Offline / no-download run:** set `EMBED_MODEL=local-hash` to build the index without downloading a model
(lower retrieval quality; for development and demos). Set `API_KEYS=key1` (and `WEB_API_KEY=key1`) to require an
`X-API-Key`.

The web client is a single page: a guided form (with example labels and live validation), a result panel with the
route, per-requirement findings, each citation shown in its regulation passage, a step timeline, feedback, and run
history. It works on a phone and in dark mode; screenshots are in `docs/screenshots/`.

Try the API directly:

```bash
curl -s -X POST localhost:8000/v1/runs -H 'content-type: application/json' \
     -d @examples/requests/source_of_protein.json          # -> {"run_id": "...", "status": "queued", ...}
curl -s localhost:8000/v1/runs/<run_id>                    # poll until completed
curl -s localhost:8000/v1/runs/<run_id>/trace              # full reconstruction of the run
```

Scale the agent workers: `docker compose up --build --scale worker=3`.
Load test (uses the stub model): `docker compose --profile loadtest run --rm locust` (see `loadtest/README.md`).

## Develop

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt langgraph langchain-core
python -m pytest                       # no Docker, network or model needed
python scripts/export_contracts.py     # regenerate contracts/*.json after changing schemas
```

## Repository map

| Path | Contents |
|---|---|
| `src/foodlabel/api` | REST interface, validation boundary, quarantine |
| `src/foodlabel/common` | settings, data contracts (`schemas.py`), run store and queue |
| `src/foodlabel/agents` | LangGraph analyst agent, tools, model client, resilience, routing |
| `src/foodlabel/verifier` | verifier agent (own service) |
| `src/foodlabel/worker` | queue consumer that runs the agent |
| `src/foodlabel/retrieval`, `ingest` | chunking, embeddings, FAISS index, ingestion pipeline |
| `src/foodlabel/llmops` | prompts registry, guardrails, cost tracking, feedback, live scoring |
| `src/foodlabel/evaluation` | evaluation metrics and batch runner |
| `src/foodlabel/observability`, `perf` | logging, tracing, metrics, trace store, cache, rate limit |
| `services/` | one folder per container (Dockerfile, requirements) |
| `deploy/` | Prometheus and Grafana configuration |
| `prompts/`, `eval/`, `contracts/`, `examples/`, `loadtest/` | versioned prompts, evaluation set, data contracts, sample requests, Locust |
| `docs/` | design documents, runbook, incident log, SLO, demo checklist |

## Documentation

`docs/architecture.md` - `docs/agent_design.md` - `docs/data_contract.md` - `docs/retrieval.md` -
`docs/evaluation.md` - `docs/guardrails.md` - `docs/observability.md` - `docs/slo.md` -
`docs/scaling.md` - `docs/runbook.md` - `docs/incident_log.md` - `docs/deployment_boundaries.md` -
`docs/demo_checklist.md` - `docs/team/` (who owns what)

## Honest limits

- The Docker images and the Prometheus/Grafana/Jaeger/cAdvisor containers were **not** built or run by the author of this
  revision (no registry access); the services were run natively and the full test-suite passes. Run `docker compose up --build` and fix anything it shows.
- Quantitative results were produced with the stub model and the offline embedder, so they describe the system's
  plumbing, not answer quality. Re-run `make eval` with a real model before reporting accuracy.
- Not covered from the syllabus: gRPC, Feast, BentoML, CrewAI, LlamaIndex, TruLens (own evaluation metrics are used instead).
