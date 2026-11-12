# Person 4 - Reliability, observability and performance

**Requirements:** A6, A7, D5, D6, D7, D8. Read `docs/observability.md`, `docs/slo.md`, `docs/scaling.md`, `docs/runbook.md`.

## Week-1 priority

1. **Commit `docs/slo.md` with the declared target before anyone runs a load test.** Review the draft numbers with the team first.
2. Make sure Prometheus targets are all up and the Grafana dashboard loads with data (`http://localhost:3000`, `http://localhost:9090/targets`).
3. Open one run in Jaeger and in `/v1/runs/{id}/trace`; confirm the trace continues from the API into the worker and verifier.

## Then

1. Run the load tests (`loadtest/README.md`), find the concurrency at which the target fails and name the bottleneck with a metric.
2. Measure the cache effect on p95 and cost per run; measure `--scale worker=3` (A7, D7).
3. Rehearse demo items 3, 8 and 9. For item 8 use `docker compose stop llm-stub` or `STUB_FAULT=http_500`.
4. Break things on purpose (kill a worker mid-run, stop Redis, stop the verifier) and write the real incidents into `docs/incident_log.md`.
   Update the runbook with what you learned. Do not invent incidents.
5. Fix dashboard panels that come up empty (cAdvisor labels differ by platform).

## Files

`src/foodlabel/observability/`, `src/foodlabel/perf/`, `services/stub_llm/`, `deploy/`, `loadtest/`, `docs/observability.md`, `slo.md`,
`scaling.md`, `runbook.md`, `incident_log.md`, `tests/observability/`.

## Note

The Prometheus, Grafana, Jaeger and cAdvisor configuration could not be run in the environment where this scaffold was built (no Docker);
the Python side (metrics, trace store, cache, rate limiter, stub model) is covered by tests. Expect to adjust queries and labels.
