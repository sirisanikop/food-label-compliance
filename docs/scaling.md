# Scaling and bottleneck analysis (A7, D6)

## Procedure

```bash
docker compose up --build -d                      # baseline: 1 worker
docker compose --profile loadtest run --rm locust # record RPS, p50, p95
docker compose up --build -d --scale worker=3     # 3 workers
docker compose --profile loadtest run --rm locust # record again
```
Repeat at increasing `LOCUST_USERS` until p95 exceeds the declared target (`docs/slo.md`). Watch while it runs: Grafana `run_queue_depth`,
per-container CPU/memory, `agent_step_duration_seconds`, Jaeger for one slow run.

## What to expect, and what to check

A run is mostly waiting on the (stubbed) model, so one worker thread per container caps throughput at roughly `1 / run_time`.
Adding workers should raise throughput until something else saturates. Candidates to rule in or out with evidence:

| Candidate | Evidence |
|---|---|
| Worker count / threads | `run_queue_depth` grows while workers are idle-waiting; `WORKER_THREADS` > 1 helps |
| Worker memory (embedding model per replica) | container memory near limit, OOM kills |
| Verifier | verification latency rising; single replica saturated |
| Redis | command latency, CPU |
| Stub model (`uvicorn` single process) | stub CPU at 100% - fix the test rig, not the system |
| Host resources | all containers share one laptop; `--scale` cannot beat the host's cores |

If scaling does not help, name the actual bottleneck with a metric, not a guess.

## Results table (fill in)

| Workers | Users | RPS | p50 | p95 | Failures | Saturated component |
|---|---|---|---|---|---|---|
| 1 | 10 | | | | | |
| 3 | 10 | | | | | |
| 1 | 20 | | | | | |
| 3 | 20 | | | | | |

## Cache effect (D7)

Run once with `LOAD_VARY=1` and once with `LOAD_VARY=0` (identical requests, run cache on); also with `ENABLE_RUN_CACHE=false`. Report p95
and cost per run (`python -m foodlabel.llmops.costs`) for each.
