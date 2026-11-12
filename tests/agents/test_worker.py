from conftest import sample_request
from foodlabel.agents.runner import AgentOutcome
from foodlabel.common import runstore
from foodlabel.worker import main as worker


def _submit(**over):
    run_id, _ = runstore.create_run(sample_request(**over), client_id="c", correlation_id="corr-1")
    runstore.enqueue(run_id)
    return run_id


def test_worker_runs_queued_request_and_caches_completed_result(monkeypatch):
    calls = []

    def fake_run_agent(run_id, request):
        calls.append(run_id)
        res = {"run_id": run_id, "route": "clear-for-print", "reason": "ok", "findings": [], "escalated": False, "degraded": False,
               "usage": {"cost_usd": 0.01}, "prompt_versions": {}}
        return AgentOutcome("completed", res, retrieved={}, steps=3, tool_calls=2)

    monkeypatch.setattr(worker, "run_agent", fake_run_agent)
    first = _submit()
    worker.handle(runstore.dequeue(timeout_s=1))
    rec = runstore.get_run(first)
    assert rec["status"] == "completed" and rec["result"]["route"] == "clear-for-print" and rec["finished_at"] >= rec["started_at"]

    second = _submit()  # identical request -> served from the run cache, no second agent run
    worker.handle(runstore.dequeue(timeout_s=1))
    rec2 = runstore.get_run(second)
    assert len(calls) == 1 and rec2["result"]["cache_hit"] is True and rec2["result"]["run_id"] == second


def test_escalated_and_degraded_results_are_not_cached(monkeypatch):
    calls = []

    def fake_run_agent(run_id, request):
        calls.append(run_id)
        return AgentOutcome("degraded", {"run_id": run_id, "route": "regulatory-review", "reason": "x", "degraded": True, "escalated": True})

    monkeypatch.setattr(worker, "run_agent", fake_run_agent)
    for _ in range(2):
        _submit()
        worker.handle(runstore.dequeue(timeout_s=1))
    assert len(calls) == 2
