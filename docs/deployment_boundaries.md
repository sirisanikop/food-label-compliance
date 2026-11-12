# Deployment boundaries (D3)

| Boundary | Justification |
|---|---|
| `api` / `worker` | **Scaling and resource profile.** The API is I/O-light and must stay responsive; workers are CPU/memory heavy (embedding model) and slow. They scale on different signals (request rate vs queue depth) and a stuck run must never block the API. |
| `worker` / `verifier` | **Failure isolation and independent release.** The verifier is a distinct agent with its own prompt and responsibility. If it fails, the worker withholds the answer and marks the run degraded; if the worker crashes, the verifier is unaffected. Its rules can be released without redeploying the heavy worker image. |
| `indexer` / `worker` | **Resource profile and release cadence.** Indexing is a batch job run when the corpus changes. Workers mount the index read-only, so a corrupt build cannot be written by a serving process. |
| `redis` | **Failure isolation.** Queue and state outlive any single worker; a crashed worker loses a run, not the queue. |
| `llm-stub` | **Test isolation.** The load test measures this system, not a provider's rate limits (D6). |
| `web` | **Independent release.** Static client behind a reverse proxy so the browser needs no CORS. |
| observability stack | **Failure isolation.** Monitoring being down must never affect request handling; exporters fail open. |

Rejected: one monolithic container (cannot scale workers without scaling the API; a worker OOM takes the API
down) and a separate embedding service (adds a network hop and a failure point for a corpus this small;
revisit if worker memory becomes the bottleneck - see `scaling.md`).
