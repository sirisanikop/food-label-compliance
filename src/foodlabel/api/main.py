"""FastAPI service: validation boundary, asynchronous run submission, status, trace, feedback.

Why asynchronous (A4): a run is a multi-step agent loop that takes tens of seconds
(several model calls plus retrieval). Holding an HTTP connection open for that long
ties up server threads, breaks behind proxies with idle timeouts and makes retries
ambiguous. The API therefore validates, enqueues and returns a run_id immediately
(202); the caller polls GET /v1/runs/{run_id}. A Redis list is the queue between
submission and execution, so the number of workers scales independently of the API.
"""
from __future__ import annotations

import hmac
import logging
import time
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from foodlabel.api import drift, quarantine
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

def require_api_key(request: Request) -> None:
    """Optional API-key authentication (Unit 2: security). Off unless API_KEYS is set.

    Keys are compared in constant time. Health, readiness, metrics and the docs stay open so that probes
    and Prometheus work; everything under /v1 requires `X-API-Key` when authentication is on.
    """
    keys = [k.strip() for k in get_settings().api_keys.split(",") if k.strip()]
    if not keys or not request.url.path.startswith("/v1"):
        return
    supplied = request.headers.get("x-api-key", "")
    if not any(hmac.compare_digest(supplied, k) for k in keys):
        metrics.AUTH_FAILURES.inc()
        raise HTTPException(status_code=401, detail="missing or invalid API key", headers={"WWW-Authenticate": "ApiKey"})


app = FastAPI(
    title="Food Label Compliance Check Service",
    version="0.1.0",
    description="Produces compliance findings with citations. Never approves a label for print.",
    dependencies=[Depends(require_api_key)],
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
        drift.observe(label.model_dump(mode="json"))
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


@app.post("/v1/validate")
def validate_only(label: LabelRequest):
    """Validate a label against the data contract without queueing a run or quarantining it.
    Used by the web form for live feedback; a failure is the same 422 field-error body."""
    return {"valid": True}


def _summary(rec: dict) -> dict:
    result = rec.get("result") or {}
    req = rec.get("request") or {}
    return {
        "run_id": rec["run_id"],
        "status": rec.get("status", "queued"),
        "product_name": req.get("product_name"),
        "route": result.get("route"),
        "degraded": bool(result.get("degraded")),
        "escalated": bool(result.get("escalated")),
        "cache_hit": bool(result.get("cache_hit")),
        "n_findings": len(result.get("findings", [])),
        "submitted_at": rec.get("submitted_at"),
        "finished_at": rec.get("finished_at"),
    }


@app.get("/v1/runs")
def list_runs(limit: int = 20):
    """Most recent runs (newest first) for the web client's history panel."""
    out = []
    for rid in runstore.list_recent_runs(max(1, min(limit, 100))):
        rec = runstore.get_run(rid)
        if rec:
            out.append(_summary(rec))
    return {"runs": out}


@app.get("/v1/runs/{run_id}/passages")
def get_run_passages(run_id: str):
    """The regulation passages this run retrieved, so a citation can be read in context."""
    if runstore.get_run(run_id) is None:
        raise HTTPException(404, "unknown run_id")
    retrieved = runstore.get_retrieved(run_id)
    return {
        "run_id": run_id,
        "passages": {
            pid: {k: p.get(k) for k in ("passage_id", "doc_id", "doc_role", "clause_ref", "section_path", "page", "text")}
            for pid, p in retrieved.items()
        },
    }


@app.get("/v1/meta")
def meta():
    """What the web client needs to describe the service: scope, corpus freshness, prompt versions, links."""
    from foodlabel.ingest.freshness import read_manifest
    from foodlabel.llmops import prompts

    s = get_settings()
    m = read_manifest() or {}
    links = dict(kv.split("=", 1) for kv in s.ui_links.split(",") if "=" in kv)
    return {
        "service": "Food Label Compliance Check",
        "version": app.version,
        "category": s.corpus_category,
        "auth_required": bool([k for k in s.api_keys.split(",") if k.strip()]),
        "prompt_versions": prompts.active_versions(("classify", "analyst", "verifier_judge")),
        "limits": {"rate_limit_per_min": s.rate_limit_per_min, "max_steps": s.max_steps, "run_timeout_s": s.run_timeout_s},
        "corpus": {
            "build_id": m.get("build_id"),
            "built_at": m.get("built_at"),
            "chunks": m.get("chunk_count"),
            "documents": [
                {"doc_id": d["doc_id"], "role": d["role"], "version": d.get("version", ""), "pages": d.get("pages")}
                for d in m.get("documents", [])
            ],
        },
        "links": links,
    }


@app.get("/v1/drift")
def drift_report():
    return drift.summary()


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
