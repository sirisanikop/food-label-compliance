import json

from fastapi.testclient import TestClient

from conftest import sample_request
from foodlabel.api.main import app
from foodlabel.common import runstore
from foodlabel.common.redis_client import get_redis

client = TestClient(app)


def test_valid_request_is_queued_and_returns_run_id():
    r = client.post("/v1/runs", json=sample_request())
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "queued" and body["run_id"]
    assert runstore.queue_depth() == 1
    assert runstore.dequeue(timeout_s=1) == body["run_id"]
    status = client.get(body["status_url"]).json()
    assert status["status"] == "queued" and status["result"] is None


def test_malformed_request_is_quarantined_and_starts_no_run():
    bad = sample_request(fssai_license_number="123", net_quantity=-5)
    r = client.post("/v1/runs", json=bad)
    assert r.status_code == 422
    body = r.json()
    assert body["run_started"] is False and body["quarantine_id"]
    fields = {e["field"] for e in body["errors"]}
    assert {"fssai_license_number", "net_quantity"} <= fields
    assert runstore.queue_depth() == 0  # no agent run was started
    rec = client.get("/v1/quarantine").json()["records"][0]
    assert rec["agent_run_started"] is False and rec["errors"]


def test_cross_field_rule_rejected():
    bad = sample_request()
    bad["nutrition"]["added_sugars_g"] = 50  # more than total sugars
    r = client.post("/v1/runs", json=bad)
    assert r.status_code == 422
    assert runstore.queue_depth() == 0


def test_unknown_field_rejected():
    r = client.post("/v1/runs", json=sample_request(surprise="x"))
    assert r.status_code == 422


def test_idempotency_key_returns_same_run():
    h = {"Idempotency-Key": "abc"}
    a = client.post("/v1/runs", json=sample_request(), headers=h).json()
    b = client.post("/v1/runs", json=sample_request(), headers=h).json()
    assert a["run_id"] == b["run_id"] and b["duplicate"] is True
    assert runstore.queue_depth() == 1


def test_rate_limit(monkeypatch):
    from foodlabel.common import config

    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "2")
    config.reload_settings()
    try:
        codes = [client.post("/v1/runs", json=sample_request(), headers={"X-Client-Id": "c1"}).status_code for _ in range(3)]
        assert codes == [202, 202, 429]
        assert client.post("/v1/runs", json=sample_request(), headers={"X-Client-Id": "c2"}).status_code == 202
    finally:
        monkeypatch.setenv("RATE_LIMIT_PER_MIN", "1000")
        config.reload_settings()


def test_feedback_requires_reason_when_negative_and_is_aggregated():
    run_id = client.post("/v1/runs", json=sample_request()).json()["run_id"]
    assert client.post(f"/v1/runs/{run_id}/feedback", json={"satisfactory": False}).status_code == 422
    assert client.post(f"/v1/runs/{run_id}/feedback", json={"satisfactory": False, "reason": "wrong threshold"}).status_code == 201
    assert client.post(f"/v1/runs/{run_id}/feedback", json={"satisfactory": True}).status_code == 201
    s = client.get("/v1/feedback/summary").json()
    assert s["total"] == 2 and s["satisfied"] == 1


def test_trace_endpoint_and_unknown_run():
    run_id = client.post("/v1/runs", json=sample_request()).json()["run_id"]
    from foodlabel.observability import trace_store

    trace_store.record_event(run_id, "tool", "search_corpus", {"query": "q"}, latency_ms=3.0)
    t = client.get(f"/v1/runs/{run_id}/trace").json()
    assert t["events"][0]["step"] == "search_corpus" and t["summary"]["tool_calls"] == 1
    assert client.get("/v1/runs/nope").status_code == 404
    assert client.get("/v1/runs/nope/trace").status_code == 404


def test_health_ready_metrics_and_correlation_header():
    assert client.get("/healthz").json()["status"] == "ok"
    assert client.get("/readyz").json()["ready"] is True
    r = client.get("/metrics")
    assert r.status_code == 200 and "runs_submitted_total" in r.text
    r = client.get("/healthz", headers={"X-Correlation-ID": "cid-123"})
    assert r.headers["x-correlation-id"] == "cid-123"
    assert json.loads(client.get("/healthz").text)
