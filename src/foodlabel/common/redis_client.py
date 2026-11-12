"""Shared Redis connection (queue, run store, trace store, caches, rate limits)."""
from __future__ import annotations

import threading

from foodlabel.common.config import get_settings

_lock = threading.Lock()
_client = None


def get_redis():
    global _client
    with _lock:
        if _client is None:
            url = get_settings().redis_url
            if url.startswith("fakeredis://"):  # tests only
                import fakeredis

                _client = fakeredis.FakeRedis(decode_responses=True)
            else:
                import redis

                _client = redis.Redis.from_url(
                    url,
                    decode_responses=True,
                    socket_timeout=5,
                    socket_connect_timeout=3,
                    health_check_interval=15,
                )
        return _client


def reset_redis_client() -> None:
    global _client
    with _lock:
        _client = None
