# Service level objective (D6)

> **STATUS: DRAFT - COMMIT THIS FILE BEFORE RUNNING ANY LOAD TEST.**
> A target set after seeing the results does not satisfy D6. The git commit timestamp of the version with the final numbers is your
> evidence. Review the numbers below with the team, change them if you disagree, commit, and only then measure.

## Declared target

| | Value |
|---|---|
| SLI | time from `POST /v1/runs` to the run reaching a terminal status (`full_run` in Locust) |
| Objective | **p95 <= 45 s** at **10 concurrent simulated users**, with **>= 99% of runs completing** (not failing) |
| Model latency assumed | stub delay `STUB_DELAY_MS=3000` per model call (a typical free-tier call), 4-6 model calls per run |
| Window | 5-minute steady state after a 1-minute ramp |

## Justification (against project section 1)

A regulatory affairs analyst checks a label before it goes to print; the cost of waiting is a person idle, not a failed transaction,
and the alternative is a manual check measured in tens of minutes. A result inside under a minute keeps the analyst in flow; beyond
about a minute they switch tasks and the value of an interactive check falls. 45 s at p95 leaves headroom under that minute for model
variance. Ten concurrent users is a department of analysts submitting around the same time, not public traffic.
Throughput is not the target: label checks are bursty and low-volume.

## How to measure

1. Set `STUB_DELAY_MS=3000` in `.env` and point `LLM_BASE_URL` at the stub (default).
2. `LOAD_VARY=1` (unique requests, uncached path) for the SLO measurement; `LOAD_VARY=0` to measure the cache effect separately.
3. `docker compose --profile loadtest run --rm locust` with `LOCUST_USERS=10`; then 20, 40, ... until p95 exceeds the target.
4. Record the table in `docs/scaling.md`: users, RPS, p50, p95, failures, and which component saturated (queue depth, worker CPU/memory, verifier, Redis).

## Result

_Not yet measured. Fill in from `docs/scaling.md` after the load test._

| Concurrency | RPS | p50 | p95 | Target met? |
|---|---|---|---|---|
| 10 | | | | |
