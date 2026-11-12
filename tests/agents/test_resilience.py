import pytest

from foodlabel.agents.resilience import CircuitBreaker, CircuitOpenError, RetryableError, call_with_policy, retry_with_backoff


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_retry_uses_exponential_backoff_and_is_bounded():
    sleeps, calls = [], []

    def fn():
        calls.append(1)
        raise RetryableError("boom")

    with pytest.raises(RetryableError):
        retry_with_backoff(fn, retries=3, base_delay_s=1.0, jitter=0.0, sleep=sleeps.append)
    assert len(calls) == 4 and sleeps == [1.0, 2.0, 4.0]


def test_retry_succeeds_after_transient_failure():
    state = {"n": 0}

    def fn():
        state["n"] += 1
        if state["n"] < 3:
            raise RetryableError("x")
        return "ok"

    assert retry_with_backoff(fn, retries=5, base_delay_s=0, sleep=lambda s: None) == "ok"


def test_breaker_opens_then_half_opens_then_closes():
    clock = Clock()
    br = CircuitBreaker("t1", fail_threshold=3, reset_s=10, clock=clock)

    def bad():
        raise RetryableError("down")

    for _ in range(3):
        with pytest.raises(RetryableError):
            br.call(bad)
    assert br.state == "open"
    with pytest.raises(CircuitOpenError):  # fails fast, provider not called
        br.call(lambda: "never")
    clock.t = 11
    assert br.state == "half_open"
    assert br.call(lambda: "probe ok") == "probe ok"
    assert br.state == "closed"


def test_failed_probe_reopens_breaker():
    clock = Clock()
    br = CircuitBreaker("t2", fail_threshold=1, reset_s=5, clock=clock)
    with pytest.raises(RetryableError):
        br.call(lambda: (_ for _ in ()).throw(RetryableError("x")))
    clock.t = 6
    with pytest.raises(RetryableError):
        br.call(lambda: (_ for _ in ()).throw(RetryableError("x")))
    assert br.state == "open"


def test_open_breaker_is_not_retried():
    br = CircuitBreaker("t3", fail_threshold=1, reset_s=100, clock=Clock())
    calls = []

    def bad():
        calls.append(1)
        raise RetryableError("x")

    with pytest.raises(RetryableError):
        br.call(bad)
    with pytest.raises(CircuitOpenError):
        call_with_policy(bad, breaker=br, retries=5, base_delay_s=0, sleep=lambda s: None)
    assert len(calls) == 1
