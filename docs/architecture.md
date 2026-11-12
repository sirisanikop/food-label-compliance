# Architecture

## Request flow

1. `POST /v1/runs` - the API validates the body against the data contract (`contracts/label_request.v1.schema.json`).
   Invalid requests are quarantined with field-level errors and **never reach the queue**.
2. The API applies the per-client rate limit, stores the run (`queued`), pushes the run id on a Redis list and
   returns `202 {run_id}` immediately. The caller polls `GET /v1/runs/{id}`.
3. A **worker** (analyst agent) pops the run id. If an identical request was already answered with the same
   prompt versions and index build, the cached result is returned. Otherwise it runs the LangGraph graph:
   `guard -> classify -> analyst <-> tools -> verify -> finalize`.
4. The analyst calls tools to retrieve provisions, discovers that a claim needs the claims regulation, searches
   again, and finishes with findings that cite passage ids and verbatim quotes.
5. The **verifier agent** (separate service) checks every citation against the retrieved passages. Failure sends the
   analyst back with a different strategy; repeated failure escalates. Nothing unverified is returned.
6. The route is derived from verified findings by code (`agents/router.py`), the output filter runs, and the result
   is stored. Every step is recorded in the run's trace.

## Why asynchronous (A4)

A run makes several model calls plus retrieval and takes tens of seconds. A synchronous endpoint would hold a
connection and a server thread for that time, hit proxy idle timeouts, make client retries ambiguous (did the run
start?), and tie API capacity to model latency. Submit-then-poll keeps the API fast, lets the number of workers
scale independently, and puts a queue between submission and execution so bursts wait instead of failing.
A callback was rejected: it needs every caller to host an endpoint, which a browser client cannot.

## Components

| Service | Role | Scales by |
|---|---|---|
| `api` | validation, quarantine, rate limit, run submission/status/trace/feedback | replicas (stateless) |
| `worker` | analyst agent, one run per thread | `--scale worker=N` |
| `verifier` | verifier agent | replicas |
| `indexer` | one-shot ingestion job | n/a |
| `redis` | queue, run store, trace store, caches, rate limits | n/a (single node) |
| `llm-stub` | stand-in model for load tests | n/a |
| `web` | static client + reverse proxy | n/a |
| `prometheus`, `grafana`, `jaeger`, `cadvisor` | observability | n/a |

See `deployment_boundaries.md` for why the boundaries are where they are.

## Data stores

Redis holds run records (7-day TTL), the queue, per-run trace events, caches, feedback and quality points.
The FAISS index and its manifest live on the `index-data` volume, written by `indexer`, read-only for workers.
