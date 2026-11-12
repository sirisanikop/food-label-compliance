"""Hand-off from the analyst agent to the verifier agent (A2, A3).

The verifier runs as its own service. If it is unreachable the analyst does NOT return
its unverified answer: the run escalates and is marked degraded.
"""
from __future__ import annotations

import logging

import httpx

from foodlabel.agents.resilience import CircuitBreaker, CircuitOpenError, RetryableError, call_with_policy
from foodlabel.common.config import get_settings
from foodlabel.common.schemas import VerifyRequest, VerifyResponse
from foodlabel.observability.correlation import HEADER, get_correlation_id

log = logging.getLogger("foodlabel.verifier_client")


class VerifierUnavailable(Exception):
    pass


class HttpVerifier:
    def __init__(self, http: httpx.Client | None = None):
        s = get_settings()
        self.s = s
        self.breaker = CircuitBreaker("verifier", s.breaker_fail_threshold, s.breaker_reset_s)
        self._http = http or httpx.Client(timeout=httpx.Timeout(s.verifier_timeout_s, connect=3.0))

    def verify(self, req: VerifyRequest) -> VerifyResponse:
        url = self.s.verifier_url.rstrip("/") + "/verify"

        def once() -> dict:
            try:
                r = self._http.post(url, json=req.model_dump(mode="json"), headers={HEADER: get_correlation_id()})
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise RetryableError(f"transport: {exc.__class__.__name__}") from exc
            if r.status_code >= 500 or r.status_code == 429:
                raise RetryableError(f"http {r.status_code}")
            r.raise_for_status()
            return r.json()

        try:
            data = call_with_policy(once, breaker=self.breaker, retries=2, base_delay_s=0.3)
        except (RetryableError, CircuitOpenError) as exc:
            raise VerifierUnavailable(str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            raise VerifierUnavailable(f"verifier rejected request: {exc.response.status_code}") from exc
        return VerifyResponse(**data)
