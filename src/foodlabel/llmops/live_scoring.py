"""Automatically score a sample of live runs against the evaluation metrics (L6).

Each scored run appends a point to a Redis list (served by GET /v1/quality and charted by
Grafana through the live_quality_score gauge). Only metrics computable without a reference
answer are used: groundedness, hallucination rate, faithfulness.
"""
from __future__ import annotations

import json
import logging
import random
import time

from foodlabel.common.config import get_settings
from foodlabel.common.redis_client import get_redis
from foodlabel.evaluation import metrics as m
from foodlabel.observability import metrics as prom

log = logging.getLogger("foodlabel.live_scoring")
KEY = "quality:points"
_WINDOW: list[dict] = []


def score_run(result: dict, retrieved: dict) -> dict:
    findings = result.get("findings", [])
    return {
        "groundedness": m.groundedness(findings, retrieved),
        "hallucination_rate": m.hallucination_rate(findings, retrieved),
        "faithfulness": m.faithfulness(findings),
    }


def maybe_score(run_id: str, request: dict, result: dict, retrieved: dict) -> None:
    if result.get("degraded") or not retrieved:
        return
    if random.random() >= get_settings().live_score_sample_rate:
        return
    try:
        point = {"ts": time.time(), "run_id": run_id, **score_run(result, retrieved)}
        r = get_redis()
        r.lpush(KEY, json.dumps(point))
        r.ltrim(KEY, 0, 4999)
        _WINDOW.append(point)
        del _WINDOW[:-50]
        for metric in ("groundedness", "hallucination_rate", "faithfulness"):
            prom.LIVE_QUALITY.labels(metric).set(sum(p[metric] for p in _WINDOW) / len(_WINDOW))
    except Exception:  # noqa: BLE001 - scoring must never break a run
        log.exception("live scoring failed", extra={"run_id": run_id})


def quality_series(limit: int = 200) -> list[dict]:
    return list(reversed([json.loads(x) for x in get_redis().lrange(KEY, 0, limit - 1)]))
