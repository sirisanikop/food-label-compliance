"""Timeouts, bounded retry with exponential backoff, circuit breaker, fallback (A5).

Failure path for the model provider:
  1. every call has a timeout (httpx timeout in agents/llm.py)
  2. retryable failures (timeout, 429, 5xx) are retried with exponential backoff + jitter,
     bounded by LLM_MAX_RETRIES
  3. consecutive failures open the circuit breaker; while open, calls fail immediately
     (CircuitOpenError) instead of piling load onto a failing provider
  4. after BREAKER_RESET_S the breaker lets one probe through (half-open)
  5. when the model is unavailable the run takes the DEGRADED path (agents/degrade.py):
     a deterministic pre-check, routed to regulatory-review, never a confident answer.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from typing import Callable, Optional, TypeVar

from foodlabel.observability import metrics

log = logging.getLogger("foodlabel.resilience")
T = TypeVar("T")


class RetryableError(Exception):
    """A transient failure: timeout, 429, 5xx, connection error."""


class CircuitOpenError(Exception):
    """Raised immediately while the breaker is open."""


_STATE_CODE = {"closed": 0, "half_open": 1, "open": 2}


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        fail_threshold: int,
        reset_s: float,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.name = name
        self.fail_threshold = fail_threshold
        self.reset_s = reset_s
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: Optional[float] = None
        self._probe_in_flight = False
        self._publish("closed")

    # -- state ---------------------------------------------------------------
    @property
    def state(self) -> str:
        with self._lock:
            return self._state_locked()

    def _state_locked(self) -> str:
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at >= self.reset_s:
            return "half_open"
        return "open"

    def _publish(self, state: str) -> None:
        metrics.BREAKER_STATE.labels(self.name).set(_STATE_CODE[state])

    # -- protocol ------------------------------------------------------------
    def allow(self) -> bool:
        with self._lock:
            st = self._state_locked()
            if st == "closed":
                return True
            if st == "half_open" and not self._probe_in_flight:
                self._probe_in_flight = True
                self._publish("half_open")
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._probe_in_flight = False
        self._publish("closed")

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._probe_in_flight = False
            if self._failures >= self.fail_threshold or self._opened_at is not None:
                if self._opened_at is None:
                    log.error("circuit breaker opened", extra={"breaker": self.name})
                self._opened_at = self._clock()
                self._publish("open")

    def call(self, fn: Callable[[], T]) -> T:
        if not self.allow():
            raise CircuitOpenError(f"circuit '{self.name}' is open")
        try:
            result = fn()
        except RetryableError:
            self.record_failure()
            raise
        except Exception:
            # client errors (4xx etc.) say nothing about provider health
            with self._lock:
                self._probe_in_flight = False
            raise
        self.record_success()
        return result


def retry_with_backoff(
    fn: Callable[[], T],
    *,
    retries: int,
    base_delay_s: float,
    max_delay_s: float = 8.0,
    jitter: float = 0.25,
    on_retry: Optional[Callable[[int, Exception], None]] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call fn; retry RetryableError up to `retries` times with exponential backoff."""
    attempt = 0
    while True:
        try:
            return fn()
        except RetryableError as exc:
            if attempt >= retries:
                raise
            delay = min(max_delay_s, base_delay_s * (2**attempt))
            delay *= 1 + random.uniform(-jitter, jitter)
            attempt += 1
            if on_retry:
                on_retry(attempt, exc)
            sleep(max(0.0, delay))


def call_with_policy(
    fn: Callable[[], T],
    *,
    breaker: CircuitBreaker,
    retries: int,
    base_delay_s: float,
    on_retry: Optional[Callable[[int, Exception], None]] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Each attempt passes through the breaker; CircuitOpenError is never retried."""
    return retry_with_backoff(
        lambda: breaker.call(fn),
        retries=retries,
        base_delay_s=base_delay_s,
        on_retry=on_retry,
        sleep=sleep,
    )
