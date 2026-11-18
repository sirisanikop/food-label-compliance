from fastapi.testclient import TestClient

from conftest import sample_request
from foodlabel.api import drift
from foodlabel.api.main import app
from foodlabel.common import config, runstore
from foodlabel.common.redis_client import get_redis

client = TestClient(app)


def test_validate_endpoint_reports_field_errors_without_queueing_or_quarantining():
    r = client.post("/v1/validate", json=sample_request(fssai_license_number="1"))
    assert r.status_code == 422 and any(e["field"] == "fssai_license_number" for e in r.json()["errors"])
    assert runstore.queue_depth() == 0 and client.get("/v1/quarantine").json()["records"] == []
    assert client.post("/v1/validate", json=sample_request()).json() == {"valid": True}


def test_run_history_and_passages():
    a = client.post("/v1/runs", json=sample_request(product_name="Bar A")).json()["run_id"]
    b = client.post("/v1/runs", json=sample_request(product_name="Bar B")).json()["run_id"]
    runs = client.get("/v1/runs").json()["runs"]
    assert [r["product_name"] for r in runs][:2] == ["Bar B", "Bar A"] and runs[0]["status"] == "queued"
    runstore.save_retrieved(a, {"p1": {"passage_id": "p1", "doc_id": "D", "doc_role": "claims", "clause_ref": "Sch I", "text": "hello", "extra": 1}})
    got = client.get(f"/v1/runs/{a}/passages").json()["passages"]["p1"]
    assert got["text"] == "hello" and "extra" not in got
    assert client.get("/v1/runs/nope/passages").status_code == 404


def test_meta_describes_scope_and_links():
    m = client.get("/v1/meta").json()
    assert m["category"] and m["auth_required"] is False and "analyst" in m["prompt_versions"] and "grafana" in m["links"]


def test_api_key_auth(monkeypatch):
    monkeypatch.setenv("API_KEYS", "k1, k2")
    config.reload_settings()
    try:
        assert client.get("/v1/meta").status_code == 401
        assert client.post("/v1/runs", json=sample_request()).status_code == 401
        assert client.get("/v1/meta", headers={"X-API-Key": "wrong"}).status_code == 401
        assert client.get("/v1/meta", headers={"X-API-Key": "k2"}).status_code == 200
        assert client.post("/v1/runs", json=sample_request(), headers={"X-API-Key": "k1"}).status_code == 202
        assert client.get("/healthz").status_code == 200 and client.get("/metrics").status_code == 200  # probes stay open
        assert "auth_failures_total 3.0" in client.get("/metrics").text
    finally:
        monkeypatch.delenv("API_KEYS")
        config.reload_settings()


def test_drift_detects_a_shift_in_submitted_values():
    base = sample_request()
    for i in range(drift.BASELINE_N):  # baseline: protein around 8 g
        r = {**base, "nutrition": {**base["nutrition"], "protein_g": 7.0 + (i % 5) * 0.5}}
        drift.observe(r)
    assert drift.summary()["alerts"] == []
    for _ in range(drift.WINDOW_N):  # a client starts sending a very different product
        drift.observe({**base, "nutrition": {**base["nutrition"], "protein_g": 30.0, "carbohydrate_g": 40}})
    s = drift.summary()
    assert "protein_g" in s["alerts"] and s["fields"]["protein_g"]["score"] > drift.DRIFT_ALERT
    assert "ingest_drift_score" in client.get("/metrics").text


def test_drift_never_breaks_submission(monkeypatch):
    monkeypatch.setattr(drift, "_publish", lambda: 1 / 0)
    assert client.post("/v1/runs", json=sample_request()).status_code == 202
