# Load-test results: `full_run` rows (for docs/slo.md and docs/scaling.md)

Measured by Siri on 19 Nov 2026, 05:03-05:52 UTC, with `loadtest/run_matrix.sh` after the shared model-cache fix in
`docker-compose.yml`. Raw Locust CSVs: `loadtest/results/<case>_stats.csv`; summary: `loadtest/results/summary.tsv`.

**Setup.** macOS, Docker Desktop (10 CPUs, 8 GB for Docker), the full compose stack on one laptop. Stub model with
`STUB_DELAY_MS=3000` (about 5 model calls per run, so about 15 s of model wait per run), `WORKER_THREADS=1`. Each case:
5 minutes, spawn rate 2 users/s, Locust polling every 0.5 s, per-run give-up at 180 s (`LOAD_RUN_TIMEOUT_S`). Before each
case the leftover queue was purged and every worker was warmed up. A Locust failure is a run still not finished after 180 s.

`full_run` = submission to terminal status. "Runs/min" is completed (non-failed) `full_run`s divided by 5 minutes.
"Worker runs/5 min" is from Prometheus (`increase(run_duration_seconds_count[5m])` per worker instance).

| Case | Workers | Users | Requests | Failures | p50 (s) | p95 (s) | p99 (s) | Avg (s) | Runs/min | Worker runs/5 min | Peak queue |
|---|---|---|---|---|---|---|---|---|---|---|---|
| uncached (`LOAD_VARY=1`) | 1 | 10 | 19 | 0 | 148 | 153 | 153 | 114 | 3.8 | 17 | 9 |
| uncached | 3 | 10 | 55 | 0 | 47 | 63 | 65 | 48 | 11.0 | 19 + 18 + 18 | 7 |
| uncached | 1 | 20 | 27 | 15 (56%) | 180 | 180 | 181 | 142 | 2.4 | 19 | 27 |
| uncached | 3 | 20 | 58 | 0 | 92 | 104 | 104 | 83 | 11.6 | 20 + 19 + 19 | 24 |
| cached (`LOAD_VARY=0`, run cache on) | 1 | 10 | 1454 | 0 | 0.53 | 0.54 | 0.55 | 0.61 | 290.8 | 1 (rest from cache) | 17 |
| identical requests, cache off (`ENABLE_RUN_CACHE=false`) | 1 | 10 | 19 | 0 | 147 | 150 | 150 | 113 | 3.8 | 19 | 9 |
| uncached, `WORKER_THREADS=4` | 1 | 10 | 77 | 0 | 36 | 41 | 42 | 35 | 15.4 | 77 | 6 |
| uncached, `WORKER_THREADS=2` | 3 | 10 | 116 | 0 | 26 | 29 | 29 | 23 | 23.2 | 41 + 39 + 39 | 11 |

The two `WORKER_THREADS` rows were measured the same day, 06:20-06:32 UTC (`summary_threads.tsv`,
`ONLY="w1t4_u10_uncached w3t2_u10_uncached" SUMMARY=loadtest/results/summary_threads.tsv ./loadtest/run_matrix.sh`).
Peak worker memory was about 410 MiB with 1, 2 or 4 threads.

Peak CPU per service (from `docker stats`, sampled every 30 s) never went above about 25%: api 17-24%, worker 14-18%,
verifier 10-16%, llm-stub 13-16%, redis 2-4%.

## SLO verdict (target in docs/slo.md, committed 12 Nov 2026: p95 <= 45 s at 10 users, >= 99% complete)

| Concurrency | Workers | Runs/min | p50 | p95 | Target met? |
|---|---|---|---|---|---|
| 10 | 1 | 3.8 | 148 s | 153 s | No (p95 3.4x target; 100% completed) |
| 10 | 3 | 11.0 | 47 s | 63 s | No (p95 1.4x target; 100% completed) |
| 10 | 1 x 4 threads | 15.4 | 36 s | 41 s | **Yes** (100% completed) |
| 10 | 3 x 2 threads | 23.2 | 26 s | 29 s | **Yes** (100% completed) |

## Bottleneck

Worker concurrency. A run holds a worker thread for about 15 s, mostly waiting on the model, so one thread finishes about
4 runs per minute (measured: 17-20 per 5 minutes per worker in every uncached case). Throughput grew in step with workers
(3.8 to 11.0 runs/min, x2.9 for x3 workers) while the queue grew and every service stayed below about 25% CPU, so it is not
the host, Redis, the API, the verifier or the stub. Locust holds one run per user in flight, so latency is roughly
(users / total worker threads) x 15 s: 10/1 x 15 = 150 s and 10/3 x 15 = 50 s, which matches the measured p50s.

Confirmed by measurement: `WORKER_THREADS=4` on one replica meets the target (p95 41 s) with no extra memory, and
3 replicas x 2 threads gives p95 29 s. The (users / threads) x 15 s estimate held: 10/4 x 15 = 37.5 s (measured p50 36 s)
and 10/6 x 15 = 25 s (measured p50 26 s).

## Cache effect (D7)

With identical requests and the run cache on, one run did the work and every repeat was served from Redis: p95 0.54 s
(that is the 0.5 s polling interval; the server-side end-to-end average was 0.1 s) and 291 runs/min on one worker.
With the cache off, the same identical requests behaved exactly like unique ones (p95 150 s vs 153 s), so the cache is the
whole difference. Each cache hit saves the 5 model calls and about 9.2k tokens of a run (from the protein run's trace).
Cost per run is $0 here because `PRICE_IN_PER_1K`/`PRICE_OUT_PER_1K` are 0 for the stub.

## Earlier attempt (kept as evidence)

`loadtest/results/attempt1_before_cache_fix/` is the first run of the same matrix. It showed 3 workers no faster than 1,
because each new worker replica spent about 5 minutes downloading the embedding model before its first run (the extra
replicas finished about 0 runs). Leftover backlog also carried over between cases. Do not use those numbers. Also, in
the second run the 3-worker/10-user warm-up stalled for 9 minutes. Grafana shows no samples at all from 05:11 to 05:18 UTC,
even for Prometheus scraping itself, so the whole Docker VM was paused (most likely the Mac sleeping or Docker Desktop's
Resource Saver). The measured window started after it (05:17:44) and is valid, with all three workers active. Keep the
laptop awake during load tests.
