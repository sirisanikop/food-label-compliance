"""Prometheus metrics for every service (D5, A6, L5, D7).

Dashboard queries for these names live in deploy/grafana/build_dashboard.py.
"""
from __future__ import annotations

import time

from prometheus_client import Counter, Gauge, Histogram, start_http_server

_LAT = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 120)
_RUN_LAT = (0.5, 1, 2, 5, 10, 15, 20, 30, 45, 60, 90, 120, 180)

# ---- HTTP (api, verifier) -------------------------------------------------
HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["service", "method", "path", "status"])
HTTP_LATENCY = Histogram("http_request_duration_seconds", "HTTP latency", ["service", "method", "path"], buckets=_LAT)

# ---- API ------------------------------------------------------------------
RUNS_SUBMITTED = Counter("runs_submitted_total", "Runs accepted and queued")
QUARANTINED = Counter("requests_quarantined_total", "Requests rejected at the ingestion boundary")
RATE_LIMITED = Counter("rate_limited_total", "Requests rejected by the rate limiter")
QUEUE_DEPTH = Gauge("run_queue_depth", "Runs waiting for a worker")

# ---- Runs / agent (A6) ----------------------------------------------------
RUNS_FINISHED = Counter("runs_finished_total", "Finished runs", ["status", "route", "degraded"])
RUN_DURATION = Histogram("run_duration_seconds", "Worker execution time of a run", buckets=_RUN_LAT)
RUN_END_TO_END = Histogram("run_end_to_end_seconds", "Submission to final result", buckets=_RUN_LAT)
RUN_QUEUE_WAIT = Histogram("run_queue_wait_seconds", "Time a run waited in the queue", buckets=_LAT)
RUN_STEPS = Histogram("agent_steps_per_run", "Agent loop steps per run", buckets=(1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20))
RUN_TOOL_CALLS = Histogram("agent_tool_calls_per_run", "Tool calls per run", buckets=(0, 1, 2, 3, 4, 5, 6, 8, 10, 15))
STEP_DURATION = Histogram("agent_step_duration_seconds", "Latency per agent step", ["step"], buckets=_LAT)
TOOL_CALLS = Counter("agent_tool_calls_total", "Tool invocations", ["tool", "outcome"])
VERIFICATIONS = Counter("verification_total", "Verifier outcomes", ["outcome"])
ESCALATIONS = Counter("escalations_total", "Escalations to a human", ["reason"])

# ---- LLM (A5, L5) ---------------------------------------------------------
LLM_CALLS = Counter("llm_calls_total", "Model calls", ["step", "outcome"])
LLM_RETRIES = Counter("llm_retries_total", "Model call retries", ["step"])
LLM_LATENCY = Histogram("llm_call_duration_seconds", "Model call latency", ["step"], buckets=_LAT)
LLM_PROMPT_TOKENS = Counter("llm_prompt_tokens_total", "Prompt tokens", ["step"])
LLM_COMPLETION_TOKENS = Counter("llm_completion_tokens_total", "Completion tokens", ["step"])
LLM_COST = Counter("llm_cost_usd_total", "Model cost in USD", ["step"])
BREAKER_STATE = Gauge("circuit_breaker_state", "0=closed 1=half-open 2=open", ["name"])

# ---- Guardrails / feedback / quality -------------------------------------
GUARDRAIL_EVENTS = Counter("guardrail_events_total", "Guardrail activations", ["kind"])
FEEDBACK = Counter("feedback_total", "Caller feedback", ["satisfactory"])
LIVE_QUALITY = Gauge("live_quality_score", "Rolling live-sample quality score", ["metric"])

# ---- Cache ----------------------------------------------------------------
CACHE_REQUESTS = Counter("cache_requests_total", "Cache lookups", ["cache", "result"])

_started = time.time()
UPTIME = Gauge("service_uptime_seconds", "Seconds since this process started", ["service"])


def init_metrics(service: str) -> None:
    """Register process-level metrics and the index-freshness collector."""
    UPTIME.labels(service).set_function(lambda: time.time() - _started)
    try:
        from foodlabel.ingest import freshness

        freshness.register_collector()
    except Exception:  # noqa: BLE001 - index freshness is optional for the api/verifier
        pass


def start_metrics_server(port: int) -> None:
    start_http_server(port)


class HTTPMetricsMiddleware:
    """ASGI middleware recording request counts and latency by route template."""

    def __init__(self, app, service: str):
        self.app = app
        self.service = service

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] == "/metrics":
            return await self.app(scope, receive, send)
        start = time.perf_counter()
        status = 500

        async def send_wrapper(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            route = scope.get("route")
            path = getattr(route, "path", None) or "unmatched"
            method = scope["method"]
            HTTP_REQUESTS.labels(self.service, method, path, str(status)).inc()
            HTTP_LATENCY.labels(self.service, method, path).observe(time.perf_counter() - start)
