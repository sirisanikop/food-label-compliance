"""Model client behind an interface we control (A5).

Talks to any OpenAI-compatible /chat/completions endpoint. Provider, key and model are
injected via LLM_BASE_URL / LLM_API_KEY / LLM_MODEL, so changing provider is a config change.
Every call has a timeout, bounded retry with backoff and passes through a circuit breaker.
Token usage and cost are recorded per step (L5) via foodlabel.llmops.costs.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from foodlabel.agents.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    RetryableError,
    call_with_policy,
)
from foodlabel.common.config import get_settings
from foodlabel.llmops import costs
from foodlabel.observability import metrics
from foodlabel.observability.tracing import span

log = logging.getLogger("foodlabel.llm")


class LLMError(Exception):
    """Non-retryable model error (bad request, auth)."""


class LLMUnavailable(Exception):
    """The model could not be reached: retries exhausted or circuit open. Triggers degradation."""


@dataclass
class LLMResponse:
    text: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: float


class LLMClient:
    def __init__(self, breaker_name: str = "llm", http: Optional[httpx.Client] = None):
        s = get_settings()
        s.require("llm_base_url", "llm_model")
        self.s = s
        self.breaker = CircuitBreaker(breaker_name, s.breaker_fail_threshold, s.breaker_reset_s)
        self._http = http or httpx.Client(timeout=httpx.Timeout(s.llm_timeout_s, connect=5.0))

    def chat(
        self,
        messages: list[dict],
        *,
        step: str,
        run_id: Optional[str] = None,
        prompt_name: Optional[str] = None,
        prompt_version: Optional[str] = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        s = self.s
        payload = {"model": s.llm_model, "messages": messages, "temperature": temperature}
        headers = {"Authorization": f"Bearer {s.llm_api_key}", "X-Agent-Step": step}
        url = s.llm_base_url.rstrip("/") + "/chat/completions"

        def once() -> dict:
            try:
                r = self._http.post(url, json=payload, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise RetryableError(f"transport: {exc.__class__.__name__}") from exc
            if r.status_code == 429 or r.status_code >= 500:
                raise RetryableError(f"http {r.status_code}")
            if r.status_code >= 400:
                raise LLMError(f"http {r.status_code}: {r.text[:200]}")
            return r.json()

        def on_retry(attempt: int, exc: Exception) -> None:
            metrics.LLM_RETRIES.labels(step).inc()
            log.warning("llm retry", extra={"step": step, "attempt": attempt, "error": str(exc)})

        t0 = time.perf_counter()
        try:
            with span("llm.call", step=step):
                data = call_with_policy(
                    once,
                    breaker=self.breaker,
                    retries=s.llm_max_retries,
                    base_delay_s=s.llm_backoff_base_s,
                    on_retry=on_retry,
                )
        except (RetryableError, CircuitOpenError) as exc:
            latency = (time.perf_counter() - t0) * 1000
            outcome = "circuit_open" if isinstance(exc, CircuitOpenError) else "exhausted"
            metrics.LLM_CALLS.labels(step, outcome).inc()
            costs.record_failed_call(run_id, step, latency, outcome, prompt_name, prompt_version)
            raise LLMUnavailable(str(exc)) from exc
        except LLMError:
            metrics.LLM_CALLS.labels(step, "client_error").inc()
            raise

        latency = (time.perf_counter() - t0) * 1000
        usage = data.get("usage") or {}
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
        pt = int(usage.get("prompt_tokens", 0))
        ct = int(usage.get("completion_tokens", 0))
        cost = costs.record_llm_call(
            run_id, step, prompt_tokens=pt, completion_tokens=ct, latency_ms=latency,
            prompt_name=prompt_name, prompt_version=prompt_version,
        )
        metrics.LLM_CALLS.labels(step, "ok").inc()
        return LLMResponse(text=text, prompt_tokens=pt, completion_tokens=ct, cost_usd=cost, latency_ms=latency)
