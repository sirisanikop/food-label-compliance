"""Correlation and run identifiers carried through every service and agent step (D5)."""
from __future__ import annotations

import contextvars
import uuid

_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="-")
_run_id: contextvars.ContextVar[str] = contextvars.ContextVar("run_id", default="-")

HEADER = "x-correlation-id"


def get_correlation_id() -> str:
    return _correlation_id.get()


def set_correlation_id(value: str) -> None:
    _correlation_id.set(value)


def get_run_id() -> str:
    return _run_id.get()


def set_run_id(value: str) -> None:
    _run_id.set(value)


def new_correlation_id() -> str:
    return uuid.uuid4().hex


class CorrelationMiddleware:
    """ASGI middleware: reads or creates X-Correlation-ID, exposes it to logs and the response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        cid = headers.get(HEADER.encode(), b"").decode() or new_correlation_id()
        token = _correlation_id.set(cid)

        async def send_with_header(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                message["headers"].append((HEADER.encode(), cid.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            _correlation_id.reset(token)
