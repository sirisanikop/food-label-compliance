"""Caller feedback: persisted and aggregated (L6)."""
from __future__ import annotations

import json
import time
from collections import Counter
from typing import Optional

from foodlabel.common.redis_client import get_redis
from foodlabel.observability import metrics

KEY = "feedback:records"


def record_feedback(
    run_id: str, *, satisfactory: bool, reason: Optional[str], client_id: str, prompt_versions: Optional[dict] = None
) -> None:
    rec = {
        "ts": time.time(), "run_id": run_id, "satisfactory": satisfactory,
        "reason": (reason or "").strip(), "client_id": client_id, "prompt_versions": prompt_versions or {},
    }
    r = get_redis()
    r.lpush(KEY, json.dumps(rec))
    r.ltrim(KEY, 0, 9999)
    metrics.FEEDBACK.labels(str(satisfactory).lower()).inc()


def summary(limit: int = 1000) -> dict:
    recs = [json.loads(x) for x in get_redis().lrange(KEY, 0, limit - 1)]
    total = len(recs)
    ok = sum(1 for r in recs if r["satisfactory"])
    by_version: dict[str, dict] = {}
    for r in recs:
        v = r.get("prompt_versions", {}).get("analyst", "unknown")
        d = by_version.setdefault(f"analyst:{v}", {"n": 0, "satisfied": 0})
        d["n"] += 1
        d["satisfied"] += int(r["satisfactory"])
    reasons = Counter(r["reason"].lower()[:80] for r in recs if not r["satisfactory"] and r["reason"])
    return {
        "total": total,
        "satisfied": ok,
        "satisfaction_rate": round(ok / total, 4) if total else None,
        "by_prompt_version": by_version,
        "top_reasons": reasons.most_common(5),
        "recent": recs[:10],
    }
