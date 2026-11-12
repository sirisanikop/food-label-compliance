"""Run state shared by the API (writes at submission) and workers (write progress/result)."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Optional

from foodlabel.common.redis_client import get_redis

RUN_TTL_S = 7 * 24 * 3600
QUEUE_KEY = "queue:runs"
RECENT_KEY = "runs:recent"


def _key(run_id: str) -> str:
    return f"run:{run_id}"


def _dump(v: Any) -> str:
    return json.dumps(v, default=str)


def _load(v: str) -> Any:
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return v


def create_run(
    request: dict,
    *,
    client_id: str,
    correlation_id: str,
    trace_ctx: Optional[dict] = None,
    idempotency_key: Optional[str] = None,
) -> tuple[str, bool]:
    """Create a queued run. Returns (run_id, created). created=False means the
    Idempotency-Key matched an earlier submission and its run_id is returned."""
    r = get_redis()
    run_id = uuid.uuid4().hex
    if idempotency_key:
        ik = f"idem:{client_id}:{idempotency_key}"
        if not r.set(ik, run_id, nx=True, ex=86400):
            return r.get(ik), False
    rec = {
        "run_id": run_id,
        "status": "queued",
        "request": request,
        "client_id": client_id,
        "correlation_id": correlation_id,
        "trace_ctx": trace_ctx or {},
        "submitted_at": time.time(),
    }
    key = _key(run_id)
    r.hset(key, mapping={k: _dump(v) for k, v in rec.items()})
    r.expire(key, RUN_TTL_S)
    r.lpush(RECENT_KEY, run_id)
    r.ltrim(RECENT_KEY, 0, 4999)
    return run_id, True


def get_run(run_id: str) -> Optional[dict]:
    raw = get_redis().hgetall(_key(run_id))
    if not raw:
        return None
    return {k: _load(v) for k, v in raw.items()}


def update_run(run_id: str, **fields: Any) -> None:
    if fields:
        get_redis().hset(_key(run_id), mapping={k: _dump(v) for k, v in fields.items()})


def save_result(run_id: str, result: dict, status: str = "completed") -> None:
    update_run(run_id, result=result, status=status, finished_at=time.time())


def save_retrieved(run_id: str, retrieved: dict) -> None:
    """Persist the passages a run retrieved (audit + live quality scoring)."""
    r = get_redis()
    r.set(f"retrieved:{run_id}", _dump(retrieved), ex=RUN_TTL_S)


def get_retrieved(run_id: str) -> dict:
    raw = get_redis().get(f"retrieved:{run_id}")
    return _load(raw) if raw else {}


def list_recent_runs(limit: int = 100) -> list[str]:
    return list(get_redis().lrange(RECENT_KEY, 0, limit - 1))


# --- queue ----------------------------------------------------------------


def enqueue(run_id: str) -> None:
    get_redis().rpush(QUEUE_KEY, run_id)


def dequeue(timeout_s: int = 2) -> Optional[str]:
    item = get_redis().blpop(QUEUE_KEY, timeout=timeout_s)
    return item[1] if item else None


def queue_depth() -> int:
    return int(get_redis().llen(QUEUE_KEY))
