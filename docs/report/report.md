# Food label compliance checker: project report

Team: Siri Sanikop, Shlok, Sreekar Bandi, Nayana (contributions in section 13).

> **Status: merged draft, 19 Nov 2026.** Siri's sections (2, 5, 6, 8) are final drafts. Sections 3, 4 and 7 were assembled
> by Siri from Shlok's design and evaluation docs and **need Shlok's review and rewording before submission**. Section 14
> (AI-use disclosure) must be checked against the course policy before submission.

## 1. Problem and scope

A regulatory affairs analyst must check a packaged-food label against FSSAI regulations before it goes to print. The
requirements are spread across two regulations that do not refer to each other: the Labelling and Display Regulations
2020 say what must be on a label, and the Advertising and Claims Regulations 2018 set the conditions a claim such as
"source of protein" must meet. The conditions are sometimes stated as a percentage of a dietary allowance that neither
regulation gives.

The system takes a structured description of one label and returns one of three routes, `clear-for-print`,
`revise-and-recheck` or `regulatory-review`, with findings that cite verbatim passages of the regulations. It is scoped
to one product category (biscuits, cookies and cereal bars). It produces evidence for a person; it never approves a label.

## 2. Architecture

**Request flow.** A client sends `POST /v1/runs`. The API validates the body against the published data contract
(`contracts/label_request.v1.schema.json`). An invalid body is quarantined with field-level errors and never reaches the
queue. A valid one is rate-limited per client, stored as `queued`, pushed onto a Redis list, and answered at once with
`202 {run_id}`. A worker pops the run and executes the LangGraph agent (`guard -> classify -> analyst <-> tools -> verify ->
finalize`). The analyst retrieves passages with hybrid BM25 + vector search over a FAISS index, and a separate verifier
service checks every citation against the retrieved text before anything is returned. The route is derived from verified
findings by code, not by the model. Every step is written to a per-run trace served at `GET /v1/runs/{id}/trace`, and the
same run is traced across api, worker and verifier in Jaeger (`docs/screenshots/12-jaeger.png`).

**Why asynchronous.** A run makes 4-6 model calls plus retrieval and verification, and takes seconds to minutes. Submit
then poll keeps the API fast and independent of model latency, makes retries unambiguous (the `run_id` exists before the
work starts), and puts a durable queue between bursts and execution, so excess load waits instead of failing. A callback
was rejected because a browser client cannot host one.

**Services and boundaries.**

| Service | Role | Boundary justification |
|---|---|---|
| `api` | validation, quarantine, rate limit, submission, status, trace, passages, feedback, metrics | stateless and I/O-light; scales on request rate and must never be blocked by a slow run |
| `worker` | analyst agent, one run per thread | CPU/memory heavy (embedding model); scales on queue depth (`--scale worker=N`) |
| `verifier` | verifier agent | failure isolation and independent release: if it fails, the answer is withheld and the run is degraded |
| `indexer` | one-shot parse, chunk, embed, index job | batch job on corpus change; writes the index volume, which serving containers mount read-only |
| `redis` | queue, run store, trace store, caches, rate limits | state outlives any worker (AOF); a crashed worker loses one run, not the queue |
| `web` | static client and reverse proxy to the API | independent release; same-origin `/api/` so the browser needs no CORS and never sees an API key |
| `llm-stub` | OpenAI-compatible stand-in model | zero-cost default, and a load test that measures this system rather than a provider's quota |
| `prometheus`, `grafana`, `jaeger`, `cadvisor` | metrics, dashboard, traces, container stats | monitoring failure never affects request handling |

Rejected: a single monolithic container (cannot scale workers without the API, and a worker OOM takes the API down) and a
separate embedding service (an extra hop and failure point for a 158-passage corpus).

## 3. Agent design and verifier _(from `docs/agent_design.md`; Shlok to review)_

**Two agents, one hand-off.** The analyst agent (worker) decides what to retrieve, compares declared values with
thresholds, and drafts findings with citations. The verifier agent (separate service, `POST /verify`) checks the draft. The
analyst never decides the route and never returns an answer the verifier has not passed; the verifier never retrieves or
writes findings.

**Graph.** Built with LangGraph. Each analyst turn the model chooses one action: call a tool (`search_corpus`, `get_clause`,
`get_request_field`, `get_reference_value`, `compute`) or `finish`. Retrieval is a tool the model may call any number of
times. Loop bounds (`MAX_STEPS`, `MAX_TOOL_CALLS`, `RUN_TIMEOUT_S`, `MAX_VERIFY_ATTEMPTS`) escalate when hit; a partial
answer is never returned.

**The difficult case.** The labelling regulation does not state claim conditions. The verifier requires every detected
claim to have a finding that cites a claims-regulation passage. If one is missing (`CLAIM_CONDITION_MISSING`), the graph
itself runs a claims-scoped search (a different retrieval strategy, not just a re-ask), adds the result to the analyst's
context and lets it answer again; after `MAX_VERIFY_ATTEMPTS` it escalates.

**Thresholds as a percentage of an allowance.** "Source of protein" is 10% of the Recommended Dietary Allowance, which the
regulation does not give. The analyst calls `get_reference_value("protein")` (54 g/day, ICMR-NIN 2020) and reports the
percentage and reference it used. The verifier checks the percentage is in the quoted text, the reference matches the
table, and threshold = percentage x reference (5.4 g per 100 g), then recomputes the status.

**Verification checks.** `CITATION_NOT_RETRIEVED`, `PASSAGE_TAMPERED` (the passage id embeds a hash of its text),
`QUOTE_NOT_IN_PASSAGE`, `NO_CITATION`, `CLAIM_CONDITION_MISSING`, `NUMERIC_MISMATCH`, `THRESHOLD_NOT_IN_QUOTE`,
`REFERENCE_UNSUPPORTED`, `DERIVATION_INVALID`, and optionally `NOT_ENTAILED` from an LLM judge. The deterministic checks are
the hard gate.

**Failure handling.** Model timeouts, 429s and 5xx are retried with exponential backoff and jitter; repeated failure opens a
circuit breaker so calls fail fast; a run whose model is unavailable takes a degraded path (deterministic pre-check, route
`regulatory-review`, `degraded: true`, no findings). Shown working in the demo rehearsal (section 10, items 6 and 8).

## 4. Retrieval, prompts and guardrails _(from `docs/retrieval.md`, `docs/guardrails.md`; Shlok to review)_

**Chunking.** Split on regulation, chapter and schedule headings, so the unit is a clause rather than a character window. A
schedule's heading is repeated on every chunk cut from it so table rows stay tied to it. The embedded text is `doc title |
section path | clause` + text; the stored text is the raw passage, because citations must be verbatim. Cross-references are
stored as `mentions` the agent can follow with `get_clause`. Each chunk carries `doc_role` (`labelling` or `claims`) so a
search can be restricted to one regulation.

**Search.** A local sentence-transformers model (`all-MiniLM-L6-v2`, no paid API) and a FAISS inner-product index, blended
with a BM25 keyword score after min-max normalising both (0.65 keyword weight). Table rows such as "11. Protein ... Source"
are found far more reliably by their exact words. Reciprocal-rank fusion was tried and scored worse (recall@4 0.47 against
0.82-0.94 on the claims questions, measured with the offline embedder), so it was not kept.

**Ingestion.** Idempotent: the build id hashes the documents, the embedding model and the chunker version, so an unchanged
corpus is not rebuilt; vectors are cached per (model, text hash), so one document can be re-embedded alone. The manifest
records build time, file hashes, chunk count and throughput, exported to Prometheus for freshness.

**Prompts.** `prompts/analyst.v1` and `analyst.v2` (v2 additionally tells the model to look for modifying provisions). The
active version is recorded on every result and trace event.

**Guardrails.** In layers: strict schema at the boundary; an input scan before any model call (instruction override, role
hijack, route forcing, prompt exfiltration, chat markup, invisible characters, toxicity), which escalates with no model call;
label data and passages wrapped as untrusted data in prompts; read-only tools; an output filter that forbids approval
language and enforces the disclaimer; and the verifier. Honest limit: pattern matching misses novel phrasings; the
structural protections (read-only tools, route computed by code, verifier) are the strong ones.

## 5. UI and usability

The web client is one static page (`services/web/index.html`, no build step) served by nginx, which also proxies `/api/` to
the API. Design goals: an analyst can enter a real label without documentation, every finding shows its evidence, and the
tool never looks like it is approving a label.

- **Guided form with examples** (`01-start.png`). Fields follow the order of a printed label. An example picker loads the
  reference cases (the "source of protein" bar, an out-of-scope product, a prompt-injection attempt).
- **Live validation** (`02-validation.png`). The form calls `POST /v1/validate`, the same contract the API enforces, so
  errors (a licence number that is not 14 digits, sugars above carbohydrate) appear next to the field before submission.
- **Progress while waiting** (`03-progress.png`). The page polls and shows each finished agent step with its latency, so a
  20-second run does not look frozen.
- **Result as a "proof sheet"** (`04-result-unmet.png`, `06-result-met.png`). The route is the headline, then the reason,
  then findings grouped as Not met / Needs a person / Met. Threshold findings show the arithmetic. Every result carries
  "This is a finding with evidence, not an approval".
- **Citation in context** (`05-citation-in-context.png`). Each citation shows the verbatim quote, document and clause, and
  a "Read in context" toggle that opens the full passage with the quote highlighted (`GET /v1/runs/{id}/passages`).
- **Escalations explained** (`07-injection-escalated.png`). A blocked or out-of-scope request says why it went to a person.
- **Trace, history and feedback** (`08-trace.png`, `13-app-trace.png`, `09-history.png`). Any run's trace (13 events, 5
  model calls and 3 tool calls for the protein case) opens in a dialog; recent checks are listed; a yes/no feedback control
  posts to `/v1/runs/{id}/feedback`.
- **Phone and dark mode** (`10-mobile.png`).

**Usability bug found and fixed.** After the load test recreated the `api` container, every UI call returned `502 Bad
Gateway`: nginx had resolved `api` once at start-up and kept the old container IP. The proxy now resolves through Docker's
DNS on each request, and the UI keeps working across `docker compose up -d`, `.env` changes and `--scale` (verified).

## 6. Deployment

- **One command.** `docker compose up --build` builds six images, runs the indexer to completion, and starts the stack once
  health checks pass. Verified on 19 Nov 2026 from a fresh `git clone` of
  `main` with empty volumes (macOS, Docker Desktop, Compose v5.6): all services healthy, the index built from scratch (158
  chunks), and the protein example completed through the API and the web proxy in 9 s. Images were cached from earlier
  builds, so a first build on a new machine takes longer (it installs PyTorch and downloads the 88 MB embedding model).
- **Configuration.** Compose reads `.env.example` (committed, no secrets, stub model by default) and then `.env`
  (git-ignored). Changing provider is three variables (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`); no credentials,
  endpoints or model names are in code.
- **Fixes made during the Docker check.** (1) The API's passages endpoint needs the index, but `api` did not mount it; it
  now mounts `index-data` read-only. (2) The nginx stale-upstream 502 (section 5). (3) Workers did not scale: each new or
  recreated worker downloaded the 88 MB embedding model on its first run (about 5 minutes here), so in the first load test
  the extra replicas finished about 0 runs. A shared `hf-cache` volume for the indexer and workers fixed it; fresh replicas
  now finish their first run within 9 s.
- **Scaling.** `docker compose up -d --scale worker=N`; the Redis queue (`BLPOP`) gives each run to exactly one worker.
  `WORKER_THREADS` sets runs per replica.
- **Troubleshooting.** The README covers WSL 2 on Windows, Compose version, `.env` format, `EMBED_MODEL=local-hash` for an
  offline start, port conflicts and stale volumes.
- **Observability.** Grafana (`11-grafana.png`), Jaeger (`12-jaeger.png`), Prometheus. On Docker Desktop cAdvisor labels
  containers by id only, so per-container CPU/memory panels are empty; `docker stats` was used during the load test.

## 7. Evaluation results _(from `docs/evaluation.md`; Shlok to review)_

**Set.** `eval/eval_set.jsonl`: 20 scenarios (five "source of protein" variants, four other claims, three labelling-only
cases, two out-of-scope products, three prompt injections, three ambiguous cases). 15 of 20 expectations are verified
against the regulation text; the out-of-scope and injection cases await a real-model confirmation run.

**Metrics** (`evaluation/metrics.py`, deterministic): groundedness (quote found verbatim in the cited passage), context
relevance (expected passages retrieved), context precision (cited passages that were expected), faithfulness (status agrees
with the recomputed comparison), and hallucination rate (findings with any unsupported element). Live traffic is sampled
and scored with the same functions (`GET /v1/quality`).

**Real-model results (small and incomplete).** The full 20-scenario evaluation was not run with a real model: the free tier
limits tokens per minute and request size. Run by hand on the low-protein case (2.5 g per 100 g; expected unmet, 5.4 g needed):

| Model (Groq free tier) | Prompt | Model calls | Searches/lookups | Route | Notes |
|---|---|---|---|---|---|
| `openai/gpt-oss-120b` | v1 | 8 | 3 | revise-and-recheck | correct derivation (10% of 54 g = 5.4 g); later runs rate-limited |
| `qwen/qwen3.8-27b` | v1 | 6 | 4 | revise-and-recheck | correct derivation; headline used the passage id as requirement id |
| `openai/gpt-oss-20b` | v1 | - | - | failed 5 of 5 | does not follow the JSON action protocol (`tool_use_failed`) |
| `qwen/qwen3.8-27b` | v2 | - | - | failed | request over the 7,000 tokens/minute limit (413) |

Findings: the derived-threshold verification works with real models; model choice matters; and retries, the breaker and the
degraded route kept every failure safe (no unsupported answer was returned). **Not available:** a v1 versus v2 comparison
(v2 never completed) and scores on the 20-scenario set.

## 8. Load test, SLO, bottleneck and cost

**Target (declared before measuring).** `docs/slo.md`, committed 12 Nov 2026: p95 of the full run (submission to result)
<= 45 s at 10 concurrent users, with >= 99% of runs completing. An answer inside a minute keeps the analyst on the task;
10 users is a department submitting at once.

**Method.** Locust submits a run, polls every 0.5 s until it finishes, and records the whole thing as one `full_run`. The
model is the stub at 3 s per call (about 5 calls per run), so the test measures this system rather than a provider's quota.
Each case ran 5 minutes on one laptop (Docker Desktop, 10 CPUs, 8 GB) via `loadtest/run_matrix.sh`, which clears leftover
queue and warms every worker first. Full rows: `loadtest/results/full_run_rows.md`.

| Workers x threads | Users | Runs/min | p50 | p95 | Failed (>180 s) | Peak queue |
|---|---|---|---|---|---|---|
| 1 x 1 | 10 | 3.8 | 148 s | 153 s | 0% | 9 |
| 3 x 1 | 10 | 11.0 | 47 s | 63 s | 0% | 7 |
| 1 x 1 | 20 | 2.4 | 180 s | 180 s | 56% | 27 |
| 3 x 1 | 20 | 11.6 | 92 s | 104 s | 0% | 24 |
| 1 x 4 | 10 | 15.4 | 36 s | 41 s | 0% | 6 |
| 3 x 2 | 10 | 23.2 | 26 s | 29 s | 0% | 11 |

**SLO met with more worker threads, not met with the default.** With the default of one thread per worker, p95 at 10
users is 153 s (1 worker) or 63 s (3 workers). With `WORKER_THREADS=4` on a single worker it is **41 s**, and with 3
workers x 2 threads **29 s**, both with 0% failures. Extra threads cost no memory (peak about 410 MiB per worker with 1 or 4
threads), because a run spends its time waiting on the model, not computing. Recommended deployment: `WORKER_THREADS=4`,
and add replicas only when that is not enough. Caveat: with a real provider, more parallel calls hit its rate limits
sooner (see section 9), so the right thread count depends on the provider's quota.

**Bottleneck: worker concurrency.** A run occupies a worker thread for about 15 s, almost all of it waiting on the model.
Each worker finished 17-20 runs per 5 minutes in every case, and three workers gave 2.9x the throughput of one, while the
queue grew and no service went above about 25% CPU. That rules out the host, Redis, the API, the verifier and the stub.
Since each Locust user keeps one run in flight, latency is about (users / worker threads) x 15 s, which matches the medians.

**Caching.** With identical requests and the run cache on, one run did the work and the rest came from Redis: p95 0.54 s
(the polling interval) and 291 runs/min on one worker. With the cache off, identical requests were as slow as unique ones
(p95 150 s). Each hit saves about 5 model calls and 9.2k tokens. Real labels are rarely identical, so this mostly helps
re-checks of an unchanged label.

**Cost.** The stub and the Groq free tier cost $0 (`PRICE_IN_PER_1K`/`PRICE_OUT_PER_1K` = 0). A protein-case run uses about
9.2k tokens over 5 model calls; cost per run is that times the provider's price, recorded per step by `llmops/costs.py`.

## 9. Reliability and incidents

`docs/incident_log.md` records only real incidents. From real-model set-up: a rejected model name (404), the free-tier rate
limit opening the breaker (429), a model emitting native tool calls (400 `tool_use_failed`, now retried), a request over the
per-minute input limit (413, mitigated with `PASSAGE_MAX_CHARS`/`SEARCH_MAX_K`), and an API key exposed in a screenshot
(revoked and replaced). From the Docker check and load test: the UI 502 after an api restart and the workers not scaling,
both fixed (sections 5 and 6). In every model failure, the system degraded to `regulatory-review` and returned no
unsupported answer.

## 10. Demonstration rehearsal (19 Nov 2026, stub model)

| # | Item | Result |
|---|---|---|
| 1 | `docker compose up --build` from a clean clone | pass: fresh `git clone` of `main`, empty volumes: all healthy, index built (158 chunks), protein run in 9 s via API and web |
| 2 | valid request -> routed recommendation with citations | pass: protein label `clear-for-print` in 9 s, citing Labelling Reg 8 and Claims Schedule I |
| 2b | revise result | pass: low-protein label `revise-and-recheck`, claim unmet, same citations |
| 3 | one run traced end to end | pass: 13-event app trace; same run in Jaeger with 29 spans across api, worker, verifier |
| 4 | malformed request quarantined | pass: 422 with field errors; `requests_quarantined_total` +1, `runs_submitted_total` and queue unchanged |
| 5 | out-of-scope -> escalation | pass: `regulatory-review`, "outside the corpus scope", after classify only |
| 6 | self-verification catches an unsupported answer | pass: `wrong_source_once` -> verify fails, claims search, passes (`clear-for-print`); `always_bad_quote` -> 3 failed verifications, escalated |
| 7 | prompt injection | pass: `regulatory-review`; trace is guardrail -> route, no model call |
| 8 | circuit breaker and degradation | pass: llm-stub stopped -> `circuit_breaker_state` 2, runs `degraded`, third run failed fast; closed again 35 s after restart |
| 9 | Locust vs target; effect of `--scale` | pass: section 8 |
| 10 | the section 4.1 case | pass: both regulations cited, threshold derived (stub); real models also derived 5.4 g (section 7) |

## 11. What did not work, and limits

- The SLO latency target is met only after raising `WORKER_THREADS` above the default of 1 (section 8); the shipped default
  still misses it.
- The 20-scenario evaluation and the v1/v2 prompt comparison were not completed with a real model (free-tier limits).
- Quantitative retrieval numbers (recall@4) were measured with the offline hash embedder, not the deployed model.
- Guardrails are pattern-based and will miss novel phrasings.
- A worker crash mid-run loses that run (the queue survives); there is no automatic re-queue sweep yet.
- Tested on macOS with Docker Desktop only, not on Windows/WSL 2 or Linux.

## 12. Syllabus coverage gaps

These course tools were not used. What the project uses instead, and why:

| Not used | Used instead | Reason |
|---|---|---|
| gRPC | REST/JSON over HTTP (FastAPI) and a Redis queue | the client is a browser; the only service-to-service call (worker -> verifier) is one request per run, where HTTP/JSON is simple and traceable |
| Feast | no feature store; features are the request fields plus a small reference table (`common/reference.py`) | there are no features shared across requests or computed offline |
| BentoML | FastAPI services packaged with Docker Compose | there is no locally hosted model to serve; the model is a remote API or the stub |
| CrewAI | LangGraph | an explicit graph with typed state, loop bounds and a code-computed route was needed |
| LlamaIndex | LangChain's FAISS wrapper plus our own chunker and BM25 hybrid | clause-aware chunking and verbatim stored text needed custom code either way |
| TruLens | our own deterministic metrics (`evaluation/metrics.py`) | cheap and repeatable, usable on live traffic; TruLens could wrap `run_agent` later for LLM-judged scores |

## 13. Team and contributions

From the git history (`git log`); to be confirmed by each member.

| Member | Contribution |
|---|---|
| Siri Sanikop | repository set-up, platform and API (`api/`, `common/`, compose, contracts), first web client, README and Docker troubleshooting, Docker verification and its three fixes, observability screenshots, load test and SLO/scaling analysis, demo rehearsal, report sections 2, 5, 6, 8, 10 |
| Shlok | analyst agent, verifier service and worker; percent-of-RDA threshold verification; UI redesign and hybrid search; real-model evaluation, provider-failure handling and incident log; report sections 3, 4, 7 |
| Sreekar Bandi | retrieval, ingestion and LLMOps files (prompts, guardrails, evaluation docs); the FSSAI corpus PDFs and metadata; chunker fixes for the real PDFs and the retrieval probe; eval-set expectations verified against the corpus |
| Nayana | reliability and observability: Grafana dashboard and provisioning, Prometheus config, SLO, scaling, runbook, observability and incident-log documents |

## 14. Use of AI tools

> **DRAFT. Check the course's AI-use policy and agree the wording as a team before submitting.** Only the first paragraph
> is known; each member must add or correct their own use.

- **Siri:** used Claude Code (Anthropic, an AI coding assistant) on 19 Nov 2026 to run and debug the Docker deployment,
  diagnose and fix the UI 502 and the worker-scaling bug, write `loadtest/run_matrix.sh`, run the load test and the demo
  rehearsal, capture the screenshots, and draft report sections 2, 5, 6, 8, 10 and the merged structure of this report.
  The commit with that work carries a `Co-Authored-By: Claude` line. Siri directed the work, reviewed the results and is
  responsible for the content.
- **Shlok:** [to fill in]
- **Sreekar:** [to fill in]
- **Nayana:** [to fill in]

All numbers in this report come from runs of the system itself (load-test CSVs, Prometheus, traces, evaluation output),
not from an AI tool.
