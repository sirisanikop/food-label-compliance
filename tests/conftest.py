"""Shared test setup: in-memory Redis, fixed configuration, no network."""
import os
import tempfile

os.environ.setdefault("REDIS_URL", "fakeredis://")
os.environ.setdefault("LLM_BASE_URL", "http://llm.test/v1")
os.environ.setdefault("LLM_API_KEY", "test")
os.environ.setdefault("LLM_MODEL", "test")
os.environ.setdefault("CORPUS_CATEGORY", "Test category")
os.environ.setdefault("QUARANTINE_DIR", tempfile.mkdtemp(prefix="quarantine-"))
os.environ.setdefault("STUB_DELAY_MS", "0")
os.environ.setdefault("LLM_BACKOFF_BASE_S", "0")
os.environ.setdefault("RATE_LIMIT_PER_MIN", "1000")
os.environ.setdefault("OTLP_ENDPOINT", "")

import pytest  # noqa: E402

from foodlabel.common.redis_client import get_redis  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_redis():
    get_redis().flushall()
    yield


def sample_request(**overrides) -> dict:
    import json
    import pathlib

    base = json.loads((pathlib.Path(__file__).resolve().parents[1] / "examples" / "requests" / "source_of_protein.json").read_text())
    base.update(overrides)
    return base
