"""Verifier agent: a separate service with its own responsibility, prompt and failure domain (A2).

POST /verify  <- VerifyRequest (draft findings + the passages the analyst retrieved)
              -> VerifyResponse (ok / issues / suggested retrieval strategy)

If VERIFIER_USE_LLM=true an entailment judge also checks that each cited passage supports its
explanation. The deterministic checks are the hard gate; if the judge model is unavailable the
response says llm_judge="skipped" rather than silently passing.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from foodlabel.agents.jsonutil import extract_json
from foodlabel.agents.llm import LLMClient, LLMError, LLMUnavailable
from foodlabel.common.config import get_settings
from foodlabel.common.schemas import VerifyIssue, VerifyRequest, VerifyResponse
from foodlabel.llmops import prompts
from foodlabel.observability import metrics
from foodlabel.observability.correlation import CorrelationMiddleware, set_run_id
from foodlabel.observability.logging import init_logging
from foodlabel.observability.tracing import init_tracing, span
from foodlabel.verifier import checks

SERVICE = "verifier"
init_logging(SERVICE)
log = logging.getLogger("foodlabel.verifier")

app = FastAPI(title="Verifier agent")
app.add_middleware(metrics.HTTPMetricsMiddleware, service=SERVICE)
app.add_middleware(CorrelationMiddleware)
init_tracing(SERVICE, app)
metrics.init_metrics(SERVICE)

_llm = None


def _judge_client() -> LLMClient:
    global _llm
    if _llm is None:
        _llm = LLMClient(breaker_name="verifier-llm")
    return _llm


def _llm_judge(req: VerifyRequest) -> tuple[list[VerifyIssue], str]:
    by_id = {p.passage_id: p for p in req.retrieved}
    p = prompts.get_prompt("verifier_judge")
    issues: list[VerifyIssue] = []
    try:
        for i, f in enumerate(req.findings):
            cited = [by_id[c.passage_id].text for c in f.citations if c.passage_id in by_id]
            if not cited:
                continue
            msgs = [
                {"role": "system", "content": p.render_system()},
                {"role": "user", "content": p.render_user(explanation=f.explanation or f.summary, status=f.status, passages="\n---\n".join(cited))},
            ]
            resp = _judge_client().chat(msgs, step="verifier_judge", run_id=req.run_id, prompt_name=p.name, prompt_version=p.version)
            data = extract_json(resp.text) or {}
            if data.get("supported") is False:
                issues.append(VerifyIssue(code="NOT_ENTAILED", finding_index=i, detail=str(data.get("reason", ""))[:200]))
        return issues, "failed" if issues else "passed"
    except (LLMUnavailable, LLMError) as exc:
        log.warning("llm judge unavailable; deterministic checks only", extra={"error": str(exc)})
        return [], "skipped"


@app.post("/verify", response_model=VerifyResponse)
def verify(req: VerifyRequest) -> VerifyResponse:
    set_run_id(req.run_id)
    with span("verifier.checks", run_id=req.run_id):
        issues, checks_run = checks.check_findings(req.findings, req.retrieved, req.claims_detected)
    judge = "skipped"
    if get_settings().verifier_use_llm and not issues:
        judge_issues, judge = _llm_judge(req)
        issues += judge_issues
        checks_run.append("llm_entailment")
    return VerifyResponse(ok=not issues, issues=issues, suggested_strategy=checks.suggest_strategy(issues),
                          checks_run=checks_run, llm_judge=judge)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def prometheus_metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
