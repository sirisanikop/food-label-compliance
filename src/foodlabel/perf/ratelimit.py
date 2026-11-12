"""Per-client rate limiting (D7).

This is a cost control, not just abuse protection: one client looping against a
multi-step agent can exhaust a model budget in minutes. Fixed one-minute window in
Redis (INCR + EXPIRE), keyed by client id. Fails open if Redis is unreachable so a
Redis blip does not take the API down; the queue and run store would already be failing.
"""
from __future__ import annotations

import time

from foodlabel.common.config import get_settings
from foodlabel.common.redis_client import get_redis


def check(client_id: str) -> tuple[bool, int]:
    """Return (allowed, retry_after_seconds)."""
    limit = get_settings().rate_limit_per_min
    if limit <= 0:
        return True, 0
    now = int(time.time())
    window = now // 60
    key = f"rl:{client_id}:{window}"
    try:
        r = get_redis()
        count = r.incr(key)
        if count == 1:
            r.expire(key, 75)
    except Exception:  # noqa: BLE001
        return True, 0
    if count > limit:
        return False, 60 - (now % 60)
    return True, 0
