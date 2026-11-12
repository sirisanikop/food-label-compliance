# Runbook (D8)

Two most likely failure modes. Each: symptom, confirmation, recovery.

## 1. Model provider outage or rate limiting (breaker open)

**Symptom.** Runs finish as `degraded` with route `regulatory-review` and reason "model provider unavailable"; the web client shows
"Degraded run". Run latency may spike first (retries with backoff).

**Confirm.**
1. Grafana: `circuit_breaker_state{name="llm"}` = 2 (open); `llm_retries_total` and `llm_calls_total{outcome!="ok"}` rising.
2. Logs: `docker compose logs worker | grep "circuit breaker opened"` and `"llm retry"` lines with `error` = `http 429` / `http 5xx` / `transport: ...`.
3. Direct check: `docker compose exec worker python -c "import urllib.request,os;print(urllib.request.urlopen(os.environ['LLM_BASE_URL'].rsplit('/v1',1)[0]).status)"`
   (or call the provider with `curl` from the host to separate a provider problem from a network problem).
4. One affected run: `GET /v1/runs/{id}/trace` shows `llm` events with `ok: false` and outcome `exhausted` or `circuit_open`.

**Recover.**
1. Provider down: nothing to restart. The breaker half-opens after `BREAKER_RESET_S` and closes by itself when a probe succeeds.
2. Rate limited (429): lower `RATE_LIMIT_PER_MIN`, stop the client that is looping (find it by `client_id` in the API logs), or wait for the quota to reset.
3. Wrong key/model/URL (a 4xx is not retried and does not trip the breaker): fix `.env`, `docker compose up -d worker verifier`.
4. Re-submit the degraded runs after recovery; they were never cached.
5. Demonstration/drill: `docker compose stop llm-stub` or `STUB_FAULT=http_500`.

## 2. Queue backlog or stuck runs (workers down, overloaded, or crashed mid-run)

**Symptom.** Runs stay `queued` or `running` for longer than `RUN_TIMEOUT_S`; callers polling forever.

**Confirm.**
1. Grafana: `run_queue_depth` rising and `runs_finished_total` flat; `up{job="worker"}` = 0 for some or all replicas.
2. `docker compose ps` (worker unhealthy/restarting) and `docker compose logs --tail=100 worker` (OOM kill, `queue read failed`, Redis errors).
3. `docker compose exec redis redis-cli llen queue:runs` (backlog) and `docker stats` (memory pressure from the embedding model).
4. Stuck run: `GET /v1/runs/{id}` shows `running` with an old `started_at`; its trace stops mid-run.

**Recover.**
1. Workers down or crashed: `docker compose up -d worker` (add `--scale worker=3` for the backlog). Queued runs are processed in order; the queue is durable (Redis AOF).
2. Memory: lower `WORKER_THREADS`, or set a memory limit and fewer replicas.
3. Run stuck in `running` after a crash: it was popped from the queue and lost. Re-submit with the same `Idempotency-Key` only if it is new (a stuck run holds the key; use a new key), or re-enqueue:
   `docker compose exec redis redis-cli rpush queue:runs <run_id>`.
4. Redis down: `docker compose up -d redis`; with AOF the queue and run records survive a restart.

## Always

Record every real incident in `docs/incident_log.md` (time, symptom, cause, fix, what you changed to prevent it).
