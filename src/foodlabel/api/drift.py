"""Basic drift monitoring at ingestion (Unit 1: data validation, schema consistency, drift at ingestion).

For each numeric field of every ACCEPTED request we keep running statistics. The first BASELINE_N requests
define the baseline (mean, std); afterwards a rolling window of the last WINDOW_N values is compared with it.
The drift score is the standardised shift of the window mean: |mean_window - mean_base| / std_base.
Scores are exported as the Prometheus gauge ingest_drift_score{field} and served at GET /v1/drift.

This is deliberately simple and explainable. It tells you "the labels being submitted now look different from
the ones the service was first used on" (a new product type, a unit mix-up, a client bug), which is exactly
the situation in which agent answers deserve less trust. A score above DRIFT_ALERT (default 3) is flagged.
"""
from __future__ import annotations

import json
import math
from typing import Optional

from foodlabel.common.redis_client import get_redis
from foodlabel.observability import metrics

BASELINE_N = 30
WINDOW_N = 30
DRIFT_ALERT = 3.0
FIELDS = {
    "net_quantity": lambda r: r.get("net_quantity"),
    "principal_display_panel_cm2": lambda r: r.get("principal_display_panel_cm2"),
    "energy_kcal": lambda r: r["nutrition"].get("energy_kcal"),
    "protein_g": lambda r: r["nutrition"].get("protein_g"),
    "total_sugars_g": lambda r: r["nutrition"].get("total_sugars_g"),
    "total_fat_g": lambda r: r["nutrition"].get("total_fat_g"),
    "sodium_mg": lambda r: r["nutrition"].get("sodium_mg"),
}
_KEY = "drift:{field}"


def _mean_std(xs: list[float]) -> tuple[float, float]:
    m = sum(xs) / len(xs)
    return m, math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def observe(request: dict) -> None:
    """Record one accepted request. Never raises: monitoring must not break submission."""
    try:
        r = get_redis()
        for field, get in FIELDS.items():
            v = get(request)
            if v is None:
                continue
            key = _KEY.format(field=field)
            r.rpush(key, float(v))
            r.ltrim(key, -(BASELINE_N + 2000), -1)
        _publish()
    except Exception:  # noqa: BLE001
        pass


def snapshot() -> dict:
    r = get_redis()
    out: dict[str, dict] = {}
    for field in FIELDS:
        xs = [float(x) for x in r.lrange(_KEY.format(field=field), 0, -1)]
        entry: dict = {"n": len(xs), "baseline_ready": len(xs) >= BASELINE_N + WINDOW_N}
        if len(xs) >= BASELINE_N:
            bm, bs = _mean_std(xs[:BASELINE_N])
            entry.update(baseline_mean=round(bm, 3), baseline_std=round(bs, 3))
            if len(xs) >= BASELINE_N + WINDOW_N:
                wm, _ = _mean_std(xs[-WINDOW_N:])
                score = abs(wm - bm) / (bs if bs > 1e-9 else max(abs(bm) * 0.1, 1.0))
                entry.update(window_mean=round(wm, 3), score=round(score, 3), alert=score > DRIFT_ALERT)
        out[field] = entry
    return out


def _publish() -> None:
    for field, e in snapshot().items():
        if "score" in e:
            metrics.INGEST_DRIFT.labels(field).set(e["score"])


def summary() -> dict:
    snap = snapshot()
    return {
        "baseline_requests": BASELINE_N, "window_requests": WINDOW_N, "alert_threshold": DRIFT_ALERT,
        "alerts": [f for f, e in snap.items() if e.get("alert")], "fields": snap,
    }
