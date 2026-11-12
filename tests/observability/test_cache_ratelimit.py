from foodlabel.common import config
from foodlabel.observability import trace_store
from foodlabel.observability.correlation import set_correlation_id
from foodlabel.perf import cache, ratelimit


def test_json_cache_roundtrip_and_miss():
    assert cache.get_json("t", "k") is None
    cache.set_json("t", "k", {"a": [1, 2]})
    assert cache.get_json("t", "k") == {"a": [1, 2]}


def test_run_cache_key_changes_with_request_prompt_or_index():
    base = cache.run_cache_key({"a": 1}, {"analyst": "v1"}, "idx1")
    assert base == cache.run_cache_key({"a": 1}, {"analyst": "v1"}, "idx1")
    assert base != cache.run_cache_key({"a": 2}, {"analyst": "v1"}, "idx1")
    assert base != cache.run_cache_key({"a": 1}, {"analyst": "v2"}, "idx1")
    assert base != cache.run_cache_key({"a": 1}, {"analyst": "v1"}, "idx2")


def test_run_cache_can_be_disabled(monkeypatch):
    monkeypatch.setenv("ENABLE_RUN_CACHE", "false")
    config.reload_settings()
    try:
        cache.put_cached_run("k", {"x": 1})
        assert cache.get_cached_run("k") is None
    finally:
        monkeypatch.delenv("ENABLE_RUN_CACHE")
        config.reload_settings()


def test_rate_limiter_is_per_client(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "3")
    config.reload_settings()
    try:
        assert [ratelimit.check("a")[0] for _ in range(4)] == [True, True, True, False]
        allowed, retry = ratelimit.check("a")
        assert not allowed and 0 < retry <= 60
        assert ratelimit.check("b")[0] is True
    finally:
        monkeypatch.delenv("RATE_LIMIT_PER_MIN")
        config.reload_settings()


def test_trace_store_reconstructs_a_run_with_cost_and_correlation():
    set_correlation_id("cid-1")
    trace_store.record_event("r1", "llm", "analyst", {"prompt": "analyst"}, latency_ms=12.0, cost_usd=0.001,
                             prompt_tokens=100, completion_tokens=20, prompt_version="v2")
    trace_store.record_event("r1", "tool", "search_corpus", {"query": "q", "passages": ["p1"]}, latency_ms=4.0)
    trace_store.record_event("r1", "verification", "verify", {"ok": True})
    events = trace_store.get_trace("r1")
    assert [e["seq"] for e in events] == [0, 1, 2] and events[0]["correlation_id"] == "cid-1"
    s = trace_store.summarize("r1")
    assert s["llm_calls"] == 1 and s["tool_calls"] == 1 and s["cost_usd"] == 0.001
    assert s["per_step"]["analyst"]["prompt_tokens"] == 100 and s["prompt_versions"] == ["v2"]
