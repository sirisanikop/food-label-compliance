# Report sections: architecture, UI and usability, deployment, load-test results

Draft by Siri (platform, performance and UI). To be merged into the team report with Shlok's sections (agent design and
verifier, retrieval and prompts, evaluation results).

## 1. Architecture

The system checks a packaged-food label against two FSSAI regulations (Labelling and Display 2020; Claims and
Advertisements 2018) for one product category, biscuits, cookies and cereal bars. It returns a route
(`clear-for-print`, `revise-and-recheck` or `regulatory-review`) with findings that cite verbatim regulation passages.

**Request flow.** The browser (or any client) sends `POST /v1/runs`. The API validates the body against the published
data contract (`contracts/label_request.v1.schema.json`). An invalid body is quarantined with field-level errors and never
reaches the queue. A valid one is rate-limited per client, stored as `queued`, pushed onto a Redis list, and answered
immediately with `202 {run_id}`. A worker pops the run and executes the LangGraph agent
(`guard -> classify -> analyst <-> tools -> verify -> finalize`). The analyst retrieves passages with hybrid BM25 + vector
search over a FAISS index, and a separate verifier service checks every citation against the retrieved text before
anything is returned. The route is derived from verified findings by code, not by the model. Every step is written to a
per-run trace that the API serves at `GET /v1/runs/{id}/trace`, and the same run is traced across api, worker and
verifier in Jaeger (`docs/screenshots/12-jaeger.png`, 25 spans for one run).

**Why asynchronous.** A run makes 4-6 model calls plus retrieval and verification and takes seconds to minutes. Submit
then poll keeps the API fast and independent of model latency, makes retries unambiguous (the `run_id` exists before
the work starts), and puts a durable queue between bursts and execution so that excess load waits instead of failing.
A callback was rejected because a browser client cannot host one.

**Services and boundaries.**

| Service | Role | Boundary justification |
|---|---|---|
| `api` | validation, quarantine, rate limit, submission, status, trace, passages, feedback, metrics | stateless and I/O-light; scales on request rate and must never be blocked by a slow run |
| `worker` | analyst agent, one run per thread | CPU/memory heavy (embedding model); scales on queue depth (`--scale worker=N`) |
| `verifier` | verifier agent | failure isolation and independent release: if it fails, the answer is withheld and the run is degraded |
| `indexer` | one-shot parse, chunk, embed, index job | batch job on corpus change; writes the index volume, which serving containers mount read-only |
| `redis` | queue, run store, trace store, caches, rate limits | state outlives any worker (AOF); a crashed worker loses one run, not the queue |
| `web` | static client and reverse proxy to the API | independent release; same-origin `/api/` so the browser needs no CORS and never sees an API key |
| `llm-stub` | OpenAI-compatible stand-in model | zero-cost default and a load test that measures this system, not a provider's quota |
| `prometheus`, `grafana`, `jaeger`, `cadvisor` | metrics, dashboard, traces, container stats | monitoring failure never affects request handling |

Rejected: a single monolithic container (cannot scale workers without the API, and a worker OOM takes the API down) and
a separate embedding service (an extra hop and failure point for a 158-passage corpus).

## 2. UI and usability

The web client is one static page (`services/web/index.html`, no build step) served by nginx, which also proxies
`/api/` to the API. Design goals were: an analyst can enter a real label without reading documentation, every finding
shows its evidence, and the tool never looks like it is approving a label.

- **Guided form with examples** (`01-start.png`). Fields follow the order of a printed label (product, pack, claims,
  nutrition). An example picker fills in the reference cases (the "source of protein" bar, an out-of-scope product, a
  prompt-injection attempt).
- **Live validation** (`02-validation.png`). The form calls `POST /v1/validate`, the same contract the API enforces,
  so field errors (for example a licence number that is not 14 digits, or sugars above carbohydrate) appear next to
  the field before submission rather than as a rejected run.
- **Progress while waiting** (`03-progress.png`). Because runs are asynchronous, the page polls and shows each
  completed agent step with its latency, so a 20-second run does not look frozen.
- **Result as a "proof sheet"** (`04-result-unmet.png`, `06-result-met.png`). The route is the headline, followed by
  the reason, then findings grouped as Not met / Needs a person / Met. Threshold findings show the arithmetic (declared
  value, required value, and the percent-of-reference calculation). Every page carries the sentence "This is a finding
  with evidence, not an approval".
- **Citation in context** (`05-citation-in-context.png`). Each citation shows the verbatim quote, the document and
  clause, and a "Read in context" toggle that opens the full regulation passage with the quote highlighted. This is
  served by `GET /v1/runs/{id}/passages`.
- **Escalations explained** (`07-injection-escalated.png`). A blocked or out-of-scope request shows why it went to a
  person, for example which guardrail rules fired.
- **Trace, history and feedback** (`08-trace.png`, `13-app-trace.png`, `09-history.png`). Any run's full trace (13 events,
  5 model calls, 3 tool calls and tokens for the protein case) opens in a dialog. Recent checks are listed, and a
  yes/no "was this useful" control posts to `/v1/runs/{id}/feedback`.
- **Phone and dark mode** (`10-mobile.png`). The layout collapses to one column and follows the system colour scheme.

**Usability fix found during testing.** After the load test recreated the `api` container, every UI call returned
`502 Bad Gateway`. nginx had resolved `api` to a container IP once at start-up and kept using the old address. The proxy
now resolves through Docker's DNS on each request (`resolver 127.0.0.11 valid=10s`, `proxy_pass $api_upstream`), so
the UI keeps working across `docker compose up -d`, `.env` changes and `--scale`.

## 3. Deployment

- **One command.** `docker compose up --build` from a clean clone builds six images, runs the indexer to completion,
  and starts the stack once health checks pass (`api` waits for `redis` healthy and for `indexer` completed
  successfully). Verified on macOS (Docker Desktop, Compose v5.6, 10 CPUs, 8 GB for Docker) on 19 Nov 2026: all
  services healthy, and the protein, out-of-scope, prompt-injection and malformed examples behaved as expected through
  both the API and the web UI.
- **Configuration.** Compose reads `.env.example` (committed, no secrets, stub model by default) and then `.env`
  (git-ignored) for overrides. Changing the model provider is three variables (`LLM_BASE_URL`, `LLM_API_KEY`,
  `LLM_MODEL`); no credentials, endpoints or model names are in code.
- **Fixes made during the Docker check.**
  1. The API's `/v1/runs/{id}/passages` endpoint (the UI's "Read in context") reads the index, but the `api` service
     did not mount it. `docker-compose.yml` now mounts `index-data` read-only into `api` and makes `api` wait for the
     indexer.
  2. The nginx stale-upstream 502 described above.
  3. **Workers did not scale.** The first load test showed 3 workers finishing no more runs than 1. Prometheus
     (`run_duration_seconds_count` per worker instance) showed the two new replicas had finished about 0 runs in 5
     minutes. Their logs showed why: each new or recreated worker downloaded the 88 MB embedding model from Hugging
     Face on its first run, which took about 5 minutes here. The image has no copy of the model, and every container
     had its own empty cache. `docker-compose.yml` now mounts a shared `hf-cache` volume at `/app/.cache` in the
     indexer and every worker, so the model is downloaded once. After the fix, three freshly recreated replicas each
     finished a run within 9 s.
- **Scaling.** `docker compose up -d --scale worker=3` adds agent workers with no other change; the queue distributes
  runs (`BLPOP`, so each run goes to exactly one worker). See section 4 for what that buys.
- **Troubleshooting.** The README has a Docker troubleshooting section: WSL 2 on Windows (clone inside the WSL
  filesystem, memory limit in `.wslconfig`), Compose version, `.env` format and re-creating containers after editing it,
  `EMBED_MODEL=local-hash` for an offline start, port conflicts and stale volumes.
- **Observability as deployed.** Grafana at `:3000` with a provisioned dashboard (`11-grafana.png`), Jaeger at `:16686`
  (`12-jaeger.png`), Prometheus at `:9090`. Limit: on Docker Desktop, cAdvisor exposes containers by id only, so the
  per-container CPU/memory panels are empty; we used `docker stats` during the load test instead.

## 4. Load-test results

**Target (declared before measuring).** `docs/slo.md`, committed on 12 Nov 2026: p95 of the full run (submission to
result) <= 45 s at 10 concurrent users, with >= 99% of runs completing. The justification is the analyst's workflow: an
answer inside a minute keeps them on the task, and 10 users is a department submitting at once.

**Method.** Locust (`loadtest/locustfile.py`) submits a run, polls every 0.5 s until it finishes, and records the whole
thing as one `full_run` request. The model is the stub with 3 s per call (about 5 calls per run), so the test measures this
system, not a provider's quota. Each case ran for 5 minutes on one laptop (Docker Desktop, 10 CPUs, 8 GB), driven by
`loadtest/run_matrix.sh`, which clears leftover queue and warms every worker before each case. Full rows:
`loadtest/results/full_run_rows.md`.

| Workers | Users | Runs/min | p50 | p95 | Failed (>180 s) | Peak queue |
|---|---|---|---|---|---|---|
| 1 | 10 | 3.8 | 148 s | 153 s | 0% | 9 |
| 3 | 10 | 11.0 | 47 s | 63 s | 0% | 7 |
| 1 | 20 | 2.4 | 180 s | 180 s | 56% | 27 |
| 3 | 20 | 11.6 | 92 s | 104 s | 0% | 24 |

**SLO not met.** At 10 users, p95 is 153 s with one worker and 63 s with three. Every run completed, so the
availability half of the target holds, but the latency half does not.

**Bottleneck: worker concurrency, with evidence.** A run occupies a worker thread for about 15 s, almost all of it waiting
on the model. Each worker finished 17-20 runs per 5 minutes in every case (Prometheus `run_duration_seconds_count` per
instance), and three workers gave 2.9x the throughput of one. Meanwhile the queue grew and no service went above about
25% CPU (`docker stats`), which rules out the host, Redis, the API, the verifier and the stub. Because each Locust user
keeps one run in flight, latency is about (users / worker threads) x 15 s: 150 s for 10/1 and 50 s for 10/3, which
matches the measured medians. At 20 users with one worker, more than half the runs exceeded the 180 s give-up time.

**What would meet the target.** About 4-5 worker threads in total for 10 users. Since the work is I/O wait,
`WORKER_THREADS=4` on one replica should be as good as four replicas without loading the embedding model four times. This
is a prediction from the measurements above, not a measured result.

**Caching (D7).** With identical requests and the run cache on, one run did the work and the rest were served from
Redis: p95 0.54 s (the polling interval) and 291 runs/min on one worker. With the cache off, identical requests took as
long as unique ones (p95 150 s), so the cache is the whole difference. Each hit saves about 5 model calls and 9.2k
tokens. Real labels are rarely identical, so this mainly helps re-checks of an unchanged label and repeated demo inputs.

**What the load test found.** The first run of this matrix showed three workers no faster than one. That was not a
capacity limit but a deployment bug: new worker replicas spent about 5 minutes downloading the embedding model before
doing any work. The shared model-cache volume (section 3) fixed it, and the numbers above are from after the fix. The
first attempt's data is kept in `loadtest/results/attempt1_before_cache_fix/`.
