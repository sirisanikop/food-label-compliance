# Load test (D6, A7)

The model is the stub service (`LLM_BASE_URL` -> `llm-stub`), so the test measures this system rather than a provider's rate limits.
Read `docs/slo.md` first: **declare and commit the target before measuring.**

```bash
# in .env: STUB_DELAY_MS=3000 for a realistic per-call latency (see docs/slo.md)
docker compose up --build -d
LOCUST_USERS=10 LOCUST_SPAWN_RATE=2 LOCUST_RUN_TIME=5m docker compose --profile loadtest run --rm locust
```

Results land in `loadtest/results/run_stats.csv` (and `_failures.csv`, `_stats_history.csv`). The row named **`full_run`** is the
end-to-end figure: its response time is submission to terminal status, polled every 0.5 s. `POST /v1/runs` and `GET /v1/runs/{id}` rows are
the individual calls and are NOT the run latency.

| Variable | Meaning |
|---|---|
| `LOCUST_USERS`, `LOCUST_SPAWN_RATE`, `LOCUST_RUN_TIME` | load shape |
| `LOAD_VARY=1` (default) | every request unique: measures the uncached path |
| `LOAD_VARY=0` | identical requests: measures the run cache |
| `POLL_INTERVAL_S`, `LOAD_RUN_TIMEOUT_S` | polling cadence and per-run give-up time |

Each simulated user sends its own `X-Client-Id` so the per-client rate limiter (a cost control) does not mask real capacity. To measure the
limiter itself, run many requests from one client id.

Scale test: `docker compose up --build -d --scale worker=3`, run again, compare (see `docs/scaling.md`).
Locust's own web UI alternative: `pip install locust && locust -f loadtest/locustfile.py --host http://localhost:8000`.
