"""Per-run event log so a whole run can be reconstructed from its run_id (D5).

Every tool call, query, retrieved passage, prompt version, per-step latency and cost,
and the verification outcome is appended here and served by GET /v1/runs/{id}/trace.
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from foodlabel.common.redis_client import get_redis
from foodlabel.observability.correlation import get_correlation_id
from foodlabel.observability.tracing import current_trace_id

TRACE_TTL_S = 7 * 24 * 3600
KINDS = ("run", "guardrail", "llm", "tool", "retrieval", "verification", "route", "cache", "error")


def _key(run_id: str) -> str:
    return f"trace:{run_id}"


def record_event(
    run_id: str,
    kind: str,
    step: str,
    data: Optional[dict] = None,
    *,
    latency_ms: Optional[float] = None,
    cost_usd: Optional[float] = None,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    prompt_version: Optional[str] = None,
    ok: bool = True,
) -> None:
    event: dict[str, Any] = {
        "ts": time.time(),
        "kind": kind,
        "step": step,
        "ok": ok,
        "correlation_id": get_correlation_id(),
        "trace_id": current_trace_id(),
        "data": data or {},
    }
    for k, v in (
        ("latency_ms", latency_ms),
        ("cost_usd", cost_usd),
        ("prompt_tokens", prompt_tokens),
        ("completion_tokens", completion_tokens),
        ("prompt_version", prompt_version),
    ):
        if v is not None:
            event[k] = v
    r = get_redis()
    key = _key(run_id)
    r.rpush(key, json.dumps(event, default=str))
    r.expire(key, TRACE_TTL_S)


def get_trace(run_id: str) -> list[dict]:
    events = [json.loads(x) for x in get_redis().lrange(_key(run_id), 0, -1)]
    for i, e in enumerate(events):
        e["seq"] = i
    return events


def summarize(run_id: str) -> dict:
    events = get_trace(run_id)
    llm = [e for e in events if e["kind"] == "llm"]
    per_step: dict[str, dict] = {}
    for e in llm:
        s = per_step.setdefault(
            e["step"], {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0, "latency_ms": 0.0}
        )
        s["calls"] += 1
        s["prompt_tokens"] += e.get("prompt_tokens", 0)
        s["completion_tokens"] += e.get("completion_tokens", 0)
        s["cost_usd"] += e.get("cost_usd", 0.0)
        s["latency_ms"] += e.get("latency_ms", 0.0)
    return {
        "events": len(events),
        "llm_calls": len(llm),
        "tool_calls": sum(1 for e in events if e["kind"] == "tool"),
        "prompt_tokens": sum(s["prompt_tokens"] for s in per_step.values()),
        "completion_tokens": sum(s["completion_tokens"] for s in per_step.values()),
        "cost_usd": round(sum(s["cost_usd"] for s in per_step.values()), 8),
        "per_step": per_step,
        "prompt_versions": sorted({e["prompt_version"] for e in llm if e.get("prompt_version")}),
    }
