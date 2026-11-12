"""Runtime configuration.

Every environment-specific value (endpoints, keys, model names, limits) is read
from environment variables at runtime. See .env.example for the full list.
Nothing environment-specific is hard-coded anywhere in the repository (D4).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _s(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _i(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _b(name: str, default: bool) -> bool:
    return os.environ.get(name, "true" if default else "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


class ConfigError(RuntimeError):
    """Raised when a required setting is missing."""


@dataclass(frozen=True)
class Settings:
    service_name: str
    log_level: str
    redis_url: str
    otlp_endpoint: str
    # --- model provider (OpenAI-compatible HTTP interface) ---
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_timeout_s: float
    llm_max_retries: int
    llm_backoff_base_s: float
    breaker_fail_threshold: int
    breaker_reset_s: float
    price_in_per_1k: float
    price_out_per_1k: float
    # --- agent loop ---
    max_steps: int
    max_tool_calls: int
    run_timeout_s: float
    tool_timeout_s: float
    max_verify_attempts: int
    corpus_category: str
    # --- verifier agent ---
    verifier_url: str
    verifier_timeout_s: float
    verifier_use_llm: bool
    # --- retrieval ---
    embed_model: str
    index_dir: str
    corpus_dir: str
    retrieval_k: int
    # --- perf ---
    rate_limit_per_min: int
    cache_ttl_s: int
    enable_run_cache: bool
    # --- worker / api ---
    worker_threads: int
    worker_metrics_port: int
    live_score_sample_rate: float
    quarantine_dir: str

    def require(self, *names: str) -> None:
        missing = [n for n in names if not getattr(self, n)]
        if missing:
            raise ConfigError(
                "Missing required setting(s): "
                + ", ".join(m.upper() for m in missing)
                + ". Set them in .env (see .env.example)."
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        service_name=_s("SERVICE_NAME", "foodlabel"),
        log_level=_s("LOG_LEVEL", "INFO"),
        redis_url=_s("REDIS_URL", "redis://redis:6379/0"),
        otlp_endpoint=_s("OTLP_ENDPOINT", ""),
        llm_base_url=_s("LLM_BASE_URL"),
        llm_api_key=_s("LLM_API_KEY"),
        llm_model=_s("LLM_MODEL"),
        llm_timeout_s=_f("LLM_TIMEOUT_S", 30),
        llm_max_retries=_i("LLM_MAX_RETRIES", 3),
        llm_backoff_base_s=_f("LLM_BACKOFF_BASE_S", 0.5),
        breaker_fail_threshold=_i("BREAKER_FAIL_THRESHOLD", 5),
        breaker_reset_s=_f("BREAKER_RESET_S", 30),
        price_in_per_1k=_f("PRICE_IN_PER_1K", 0.0),
        price_out_per_1k=_f("PRICE_OUT_PER_1K", 0.0),
        max_steps=_i("MAX_STEPS", 12),
        max_tool_calls=_i("MAX_TOOL_CALLS", 10),
        run_timeout_s=_f("RUN_TIMEOUT_S", 90),
        tool_timeout_s=_f("TOOL_TIMEOUT_S", 10),
        max_verify_attempts=_i("MAX_VERIFY_ATTEMPTS", 2),
        corpus_category=_s("CORPUS_CATEGORY", ""),
        verifier_url=_s("VERIFIER_URL", "http://verifier:8001"),
        verifier_timeout_s=_f("VERIFIER_TIMEOUT_S", 20),
        verifier_use_llm=_b("VERIFIER_USE_LLM", False),
        embed_model=_s("EMBED_MODEL"),
        index_dir=_s("INDEX_DIR", "/app/data/index"),
        corpus_dir=_s("CORPUS_DIR", "/app/data/corpus"),
        retrieval_k=_i("RETRIEVAL_K", 5),
        rate_limit_per_min=_i("RATE_LIMIT_PER_MIN", 30),
        cache_ttl_s=_i("CACHE_TTL_S", 3600),
        enable_run_cache=_b("ENABLE_RUN_CACHE", True),
        worker_threads=_i("WORKER_THREADS", 1),
        worker_metrics_port=_i("WORKER_METRICS_PORT", 9100),
        live_score_sample_rate=_f("LIVE_SCORE_SAMPLE_RATE", 0.1),
        quarantine_dir=_s("QUARANTINE_DIR", "/app/data/quarantine"),
    )


def reload_settings() -> Settings:
    """Clear the cache (used by tests after changing environment variables)."""
    get_settings.cache_clear()
    return get_settings()
