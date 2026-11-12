"""Quarantine for requests rejected at the ingestion boundary (D1, demo item 4).

A rejected request is stored with its field-level errors and is never enqueued,
so no agent run starts for it.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

from foodlabel.common.config import get_settings
from foodlabel.common.redis_client import get_redis

QUARANTINE_KEY = "quarantine:records"
MAX_PAYLOAD_CHARS = 20000


def _payload_to_jsonable(payload: Any) -> Any:
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", errors="replace")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            pass
    text = json.dumps(payload, default=str)
    if len(text) > MAX_PAYLOAD_CHARS:
        return {"truncated": True, "preview": text[:MAX_PAYLOAD_CHARS]}
    return payload


def reject(*, payload: Any, errors: list[dict], client_id: str, correlation_id: str) -> str:
    qid = uuid.uuid4().hex
    record = {
        "quarantine_id": qid,
        "received_at": time.time(),
        "client_id": client_id,
        "correlation_id": correlation_id,
        "errors": errors,
        "payload": _payload_to_jsonable(payload),
        "agent_run_started": False,
    }
    line = json.dumps(record, default=str)
    r = get_redis()
    r.lpush(QUARANTINE_KEY, line)
    r.ltrim(QUARANTINE_KEY, 0, 999)
    try:  # durable copy on the quarantine volume
        d = get_settings().quarantine_dir
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "rejected.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    return qid


def list_records(limit: int = 50) -> list[dict]:
    return [json.loads(x) for x in get_redis().lrange(QUARANTINE_KEY, 0, limit - 1)]
