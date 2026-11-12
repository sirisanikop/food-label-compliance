"""Locust load test measuring a COMPLETE run: submission -> polling -> final result (D6).

The recorded request named "full_run" has response_time = submission to terminal status,
so Locust's p50/p95 for that row are the end-to-end figures to compare with the SLO.
Run against the stack with the model replaced by the stub (LLM_BASE_URL -> llm-stub).

  LOAD_VARY=1  (default) every request is unique  -> measures the uncached path
  LOAD_VARY=0                identical requests    -> measures the effect of the run cache
"""
import copy
import json
import os
import pathlib
import time
import uuid

from locust import HttpUser, between, events, task

VARY = os.environ.get("LOAD_VARY", "1") == "1"
POLL_INTERVAL_S = float(os.environ.get("POLL_INTERVAL_S", "0.5"))
RUN_TIMEOUT_S = float(os.environ.get("LOAD_RUN_TIMEOUT_S", "180"))

_SAMPLE = pathlib.Path(__file__).resolve().parents[1] / "examples" / "requests" / "source_of_protein.json"
if not _SAMPLE.exists():  # inside the locust container only ./loadtest is mounted
    _SAMPLE = pathlib.Path(__file__).parent / "sample_request.json"
BASE = json.loads(_SAMPLE.read_text())
TERMINAL = {"completed", "degraded", "failed"}


class LabelCheckUser(HttpUser):
    wait_time = between(0.5, 2)

    def on_start(self):
        # distinct client id per simulated user so the per-client rate limiter
        # (which is a cost control) does not mask the system's real capacity
        self.client_id = f"locust-{uuid.uuid4().hex[:8]}"

    @task
    def full_run(self):
        payload = copy.deepcopy(BASE)
        if VARY:
            payload["product_name"] = f"{BASE['product_name']} {uuid.uuid4().hex[:8]}"
        headers = {"X-Client-Id": self.client_id}
        start = time.perf_counter()
        exc = None
        status = "timeout"
        with self.client.post("/v1/runs", json=payload, headers=headers, name="POST /v1/runs", catch_response=True) as r:
            if r.status_code != 202:
                r.failure(f"submit {r.status_code}")
                return
            run_id = r.json()["run_id"]
        while time.perf_counter() - start < RUN_TIMEOUT_S:
            time.sleep(POLL_INTERVAL_S)
            with self.client.get(f"/v1/runs/{run_id}", headers=headers, name="GET /v1/runs/{id}", catch_response=True) as g:
                if g.status_code != 200:
                    g.failure(f"poll {g.status_code}")
                    continue
                status = g.json()["status"]
                if status in TERMINAL:
                    break
        elapsed_ms = (time.perf_counter() - start) * 1000
        if status != "completed":
            exc = Exception(f"run ended as {status}")
        events.request.fire(
            request_type="RUN", name="full_run", response_time=elapsed_ms,
            response_length=0, exception=exc, context={},
        )
