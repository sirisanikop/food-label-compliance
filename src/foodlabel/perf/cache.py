"""Caching (D7): query embeddings, retrieval results and whole runs.

Why these three:
  * query embeddings - repeated/near-identical queries across runs; saves CPU in workers.
  * retrieval results - keyed by (query, doc filter, k, index build id); invalidated
    automatically when the index is rebuilt because the build id is part of the key.
  * whole runs - keyed by (normalised request, prompt versions, index build id). The most
    valuable: it skips every model call. Only completed, non-degraded runs are cached.

Measure the effect with loadtest/ (LOAD_VARY=0 vs 1) and report p95 and cost per run.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from foodlabel.common.config import get_settings
from foodlabel.common.redis_client import get_redis
from foodlabel.observability import metrics


def digest(*parts: Any) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(json.dumps(p, sort_keys=True, default=str).encode())
        h.update(b"\x1f")
    return h.hexdigest()


def _k(ns: str, key: str) -> str:
    return f"cache:{ns}:{key}"


def get_json(ns: str, key: str) -> Optional[Any]:
    try:
        raw = get_redis().get(_k(ns, key))
    except Exception:  # noqa: BLE001 - a cache outage must never fail a run
        return None
    metrics.CACHE_REQUESTS.labels(ns, "hit" if raw else "miss").inc()
    return json.loads(raw) if raw else None


def set_json(ns: str, key: str, value: Any, ttl: Optional[int] = None) -> None:
    try:
        get_redis().set(_k(ns, key), json.dumps(value, default=str), ex=ttl or get_settings().cache_ttl_s)
    except Exception:  # noqa: BLE001
        pass


def run_cache_key(request: dict, prompt_versions: dict, index_build_id: Optional[str]) -> str:
    return digest(request, prompt_versions, index_build_id)


def get_cached_run(key: str) -> Optional[dict]:
    if not get_settings().enable_run_cache:
        return None
    return get_json("run", key)


def put_cached_run(key: str, result: dict) -> None:
    if get_settings().enable_run_cache:
        set_json("run", key, result)
