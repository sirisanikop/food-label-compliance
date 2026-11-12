"""Analyst worker: pulls run ids from the queue and executes the agent graph.

Scale with `docker compose up --scale worker=3`. Each replica runs WORKER_THREADS loops and
exposes Prometheus metrics on WORKER_METRICS_PORT (discovered via DNS service discovery).
"""
from __future__ import annotations

import logging
import signal
import threading
import time

from foodlabel.agents.runner import PROMPTS_USED, run_agent
from foodlabel.common import runstore
from foodlabel.common.config import get_settings
from foodlabel.llmops import live_scoring, prompts
from foodlabel.observability import metrics, trace_store
from foodlabel.observability.correlation import set_correlation_id, set_run_id
from foodlabel.observability.logging import init_logging
from foodlabel.observability.tracing import continue_trace, init_tracing
from foodlabel.perf import cache

SERVICE = "worker"
log = logging.getLogger("foodlabel.worker")
_stop = threading.Event()


def _index_build_id():
    try:
        from foodlabel.ingest.freshness import index_build_id

        return index_build_id()
    except Exception:  # noqa: BLE001
        return None


def handle(run_id: str) -> None:
    rec = runstore.get_run(run_id)
    if rec is None:
        log.warning("dequeued unknown run", extra={"run_id": run_id})
        return
    set_correlation_id(rec.get("correlation_id", "-"))
    set_run_id(run_id)
    started = time.time()
    metrics.RUN_QUEUE_WAIT.observe(max(0.0, started - rec.get("submitted_at", started)))
    runstore.update_run(run_id, status="running", started_at=started)
    request = rec["request"]

    with continue_trace("run", rec.get("trace_ctx"), run_id=run_id):
        versions = prompts.active_versions(PROMPTS_USED)
        build_id = _index_build_id()
        key = cache.run_cache_key(request, versions, build_id)
        cached = cache.get_cached_run(key)
        if cached:
            result = {**cached, "run_id": run_id, "cache_hit": True, "usage": {}}
            trace_store.record_event(run_id, "cache", "run_cache_hit", {"key": key[:16]})
            _finish(run_id, rec, "completed", result)
            return

        outcome = run_agent(run_id, request)
        outcome.result["index_build_id"] = build_id
        if outcome.retrieved:
            runstore.save_retrieved(run_id, outcome.retrieved)
        if outcome.status == "completed" and not outcome.result.get("escalated"):
            cache.put_cached_run(key, outcome.result)
        _finish(run_id, rec, outcome.status, outcome.result)
        live_scoring.maybe_score(run_id, request, outcome.result, outcome.retrieved)


def _finish(run_id: str, rec: dict, status: str, result: dict) -> None:
    runstore.save_result(run_id, result, status=status)
    now = time.time()
    metrics.RUN_END_TO_END.observe(max(0.0, now - rec.get("submitted_at", now)))
    metrics.RUNS_FINISHED.labels(status, result.get("route", "none"), str(bool(result.get("degraded")))).inc()
    log.info("run finished", extra={"status": status, "route": result.get("route")})


def loop() -> None:
    while not _stop.is_set():
        try:
            run_id = runstore.dequeue(timeout_s=2)
        except Exception:  # noqa: BLE001 - Redis hiccup: back off, keep the worker alive
            log.exception("queue read failed")
            time.sleep(2)
            continue
        if not run_id:
            continue
        try:
            handle(run_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("run handler crashed", extra={"run_id": run_id})
            try:
                runstore.update_run(run_id, status="failed", error=repr(exc), finished_at=time.time())
                metrics.RUNS_FINISHED.labels("failed", "none", "False").inc()
            except Exception:  # noqa: BLE001
                pass


def main() -> None:
    init_logging(SERVICE)
    init_tracing(SERVICE)
    metrics.init_metrics(SERVICE)
    s = get_settings()
    metrics.start_metrics_server(s.worker_metrics_port)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: _stop.set())
    threads = [threading.Thread(target=loop, name=f"loop-{i}", daemon=True) for i in range(s.worker_threads)]
    for t in threads:
        t.start()
    log.info("worker started", extra={"threads": s.worker_threads})
    while not _stop.is_set():
        time.sleep(0.5)
    log.info("worker stopping")


if __name__ == "__main__":
    main()
