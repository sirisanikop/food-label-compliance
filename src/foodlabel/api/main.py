"""FastAPI service: validation boundary, asynchronous run submission, status, trace, feedback.

Why asynchronous (A4): a run is a multi-step agent loop that takes tens of seconds
(several model calls plus retrieval). Holding an HTTP connection open for that long
ties up server threads, breaks behind proxies with idle timeouts and makes retries
ambiguous. The API therefore validates, enqueues and returns a run_id immediately
(202); the caller polls GET /v1/runs/{run_id}. A Redis list is the queue between
submission and execution, so the number of workers scales independently of the API.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from foodlabel.api import quarantine
from foodlabel.common import runstore
from foodlabel.common.config import get_settings
from foodlabel.common.redis_client import get_redis
from foodlabel.common.schemas import (
    FeedbackIn,
    LabelRequest,
    RunStatus,
    RunView,
    SubmitResponse,
)
from foodlabel.observability import metrics, trace_store
from foodlabel.observability.correlation import CorrelationMiddleware, get_correlation_id
from foodlabel.observability.logging import init_logging
from foodlabel.observability.tracing import init_tracing, inject_context
from foodlabel.perf import ratelimit

SERVICE = "api"
init_logging(SERVICE)
log = logging.getLogger("foodlabel.api")

app = FastAPI(
    title="Food Label Compliance Check Service",
    version="0.1.0",
    description="Produces compliance findings with citations. Never approves a label for print.",
)
app.add_middleware(metrics.HTTPMetricsMiddleware, service=SERVICE)
app.add_middleware(CorrelationMiddleware)
init_tracing(SERVICE, app)
metrics.init_metrics(SERVICE)


def client_identity(request: Request) -> str:
    return request.headers.get("x-client-id") or (
        request.client.host if request.client else "unknown"
    )


# ---------------------------------------------------------------- validation
@app.exception_handler(RequestValidationError)
async def on_validation_error(request: Request, exc: RequestValidationError):
    errors = [
        {
            "field": ".".join(str(p) for p in e.get("loc", ()) if p != "body"),
            "type": e.get("type"),
            "message": e.get("msg"),
        }
        for e in exc.errors()
    ]
    body = {"detail": "request rejected at the ingestion boundary", "errors": errors}
    if request.url.path == "/v1/runs" and request.method == "POST":
        qid = quarantine.reject(
            payload=exc.body,
            errors=errors,
            client_id=client_identity(request),
            correlation_id=get_correlation_id(),
        )
        metrics.QUARANTINED.inc()
        body.update({"quarantine_id": qid, "run_started": False})
        log.warning("request quarantined", extra={"quarantine_id": qid, "n_errors": len(errors)})
    return JSONResponse(status_code=422, content=body)


# ---------------------------------------------------------------- runs
@app.post("/v1/runs", status_code=202, response_model=SubmitResponse)
def submit_run(
    label: LabelRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None),
):
    client_id = client_identity(request)
    allowed, retry_after = ratelimit.check(client_id)
    if not allowed:
        metrics.RATE_LIMITED.inc()
        raise HTTPException(
            status_code=429,
            detail="rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
    run_id, created = runstore.create_run(
        label.model_dump(mode="json"),
        client_id=client_id,
        correlation_id=get_correlation_id(),
        trace_ctx=inject_context(),
        idempotency_key=idempotency_key,
    )
    if created:
        runstore.enqueue(run_id)
        metrics.RUNS_SUBMITTED.inc()
        log.info("run submitted", extra={"run_id": run_id, "client_id": client_id})
    return SubmitResponse(
        run_id=run_id,
        status=RunStatus.QUEUED,
        status_url=f"/v1/runs/{run_id}",
        trace_url=f"/v1/runs/{run_id}/trace",
        duplicate=not created,
    )


@app.get("/v1/runs/{run_id}", response_model=RunView)
def get_run(run_id: str):
    rec = runstore.get_run(run_id)
    if rec is None:
        raise HTTPException(404, "unknown run_id")
    return RunView(
        run_id=run_id,
        status=rec.get("status", "queued"),
        submitted_at=rec.get("submitted_at", 0.0),
        started_at=rec.get("started_at"),
        finished_at=rec.get("finished_at"),
        result=rec.get("result"),
        error=rec.get("error"),
    )


@app.get("/v1/runs/{run_id}/trace")
def get_trace(run_id: str):
    """Everything needed to reconstruct a run (D5)."""
    if runstore.get_run(run_id) is None:
        raise HTTPException(404, "unknown run_id")
    return {
        "run_id": run_id,
        "events": trace_store.get_trace(run_id),
        "summary": trace_store.summarize(run_id),
    }


# ---------------------------------------------------------------- feedback / quality
@app.post("/v1/runs/{run_id}/feedback", status_code=201)
def post_feedback(run_id: str, body: FeedbackIn, request: Request):
    from foodlabel.llmops import feedback

    rec = runstore.get_run(run_id)
    if rec is None:
        raise HTTPException(404, "unknown run_id")
    feedback.record_feedback(
        run_id,
        satisfactory=body.satisfactory,
        reason=body.reason,
        client_id=client_identity(request),
        prompt_versions=(rec.get("result") or {}).get("prompt_versions", {}),
    )
    return {"recorded": True}


@app.get("/v1/feedback/summary")
def feedback_summary():
    from foodlabel.llmops import feedback

    return feedback.summary()


@app.get("/v1/quality")
def quality_series(limit: int = 200):
    from foodlabel.llmops import live_scoring

    return {"points": live_scoring.quality_series(limit)}


@app.get("/v1/quarantine")
def list_quarantine(limit: int = 50):
    return {"records": quarantine.list_records(limit)}


# ---------------------------------------------------------------- ops
@app.get("/healthz")
def healthz():
    return {"status": "ok", "time": time.time()}


@app.get("/readyz")
def readyz(response: Response):
    checks = {}
    try:
        checks["redis"] = bool(get_redis().ping())
    except Exception:  # noqa: BLE001
        checks["redis"] = False
    ready = all(checks.values())
    if not ready:
        response.status_code = 503
    return {"ready": ready, "checks": checks, "queue_depth": _safe_depth()}


def _safe_depth() -> Optional[int]:
    try:
        return runstore.queue_depth()
    except Exception:  # noqa: BLE001
        return None


@app.get("/metrics")
def prometheus_metrics():
    try:
        metrics.QUEUE_DEPTH.set(runstore.queue_depth())
    except Exception:  # noqa: BLE001
        pass
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
