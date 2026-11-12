import json
import pathlib

from foodlabel.common.schemas import LabelRequest
from foodlabel.evaluation import metrics as m
from foodlabel.evaluation.compare import compare
from foodlabel.llmops import costs, feedback, prompts

TEXT = "Source of protein: not less than 6 g of protein per 100 g."
RETRIEVED = {"p1": {"passage_id": "p1", "doc_role": "claims", "clause_ref": "Sch I", "text": TEXT}}


def f(quote="not less than 6 g of protein", status="met", values=None, pid="p1"):
    return {"requirement_id": "R", "status": status, "citations": [{"passage_id": pid, "quote": quote}], "values": values}


def test_groundedness():
    assert m.groundedness([f()], RETRIEVED) == 1.0
    assert m.groundedness([f(), f(quote="invented text")], RETRIEVED) == 0.5
    assert m.groundedness([f(pid="zz")], RETRIEVED) == 0.0
    assert m.groundedness([], RETRIEVED) == 1.0
    assert m.groundedness([{"requirement_id": "R", "status": "met", "citations": []}], RETRIEVED) == 0.0


def test_hallucination_rate():
    assert m.hallucination_rate([f()], RETRIEVED) == 0.0
    assert m.hallucination_rate([f(), f(quote="made up")], RETRIEVED) == 0.5
    assert m.hallucination_rate([{"requirement_id": "R", "status": "met", "citations": []}], RETRIEVED) == 1.0
    v = {"declared": 8, "comparator": "gte", "threshold": 10}  # 10 is not in the quote
    assert m.hallucination_rate([f(values=v)], RETRIEVED) == 1.0


def test_faithfulness_recomputes_status():
    ok = f(status="met", values={"declared": 8, "comparator": "gte", "threshold": 6})
    bad = f(status="met", values={"declared": 2, "comparator": "gte", "threshold": 6})
    assert m.faithfulness([ok]) == 1.0 and m.faithfulness([ok, bad]) == 0.5 and m.faithfulness([f()]) == 1.0


def test_context_relevance_and_precision():
    exp = [{"doc_role": "claims", "clause_contains": "source of protein"}]
    assert m.context_relevance(RETRIEVED, exp) == 1.0
    assert m.context_relevance(RETRIEVED, [{"doc_role": "labelling", "clause_contains": "x"}]) == 0.0
    assert m.context_relevance(RETRIEVED, []) is None
    assert m.context_precision([f()], RETRIEVED, exp) == 1.0


def test_eval_set_is_valid_and_complete():
    path = pathlib.Path(__file__).resolve().parents[2] / "eval" / "eval_set.jsonl"
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    assert len(rows) >= 20
    assert len({r["id"] for r in rows}) == len(rows)
    assert any(r["group"] == "claim_protein" for r in rows)
    for r in rows:
        LabelRequest.model_validate(r["request"])


def test_compare_reports_direction_of_improvement():
    a = {"aggregate": {"groundedness": 0.6, "hallucination_rate": 0.3}}
    b = {"aggregate": {"groundedness": 0.8, "hallucination_rate": 0.1}}
    rows = {r["metric"]: r for r in compare(a, b)}
    assert rows["groundedness"]["better"] == "b" and rows["hallucination_rate"]["better"] == "b"


def test_prompt_versions_are_loadable_and_distinct():
    assert set(prompts.list_versions("analyst")) >= {"v1", "v2"}
    v1, v2 = prompts.get_prompt("analyst", "v1"), prompts.get_prompt("analyst", "v2")
    assert v1.version == "v1" and v2.version == "v2" and v1.system != v2.system
    assert "DETECTED_CLAIMS: a | b" in v1.render_user(claims="a | b", category="c", label_data="{}")
    assert "Procedure" in v2.system


def test_cost_report_attributes_per_step():
    from foodlabel.common import runstore

    rid, _ = runstore.create_run({}, client_id="c", correlation_id="x")
    costs.record_llm_call(rid, "analyst", prompt_tokens=900, completion_tokens=100, latency_ms=5)
    costs.record_llm_call(rid, "classify", prompt_tokens=100, completion_tokens=10, latency_ms=5)
    rep = costs.cost_report(10)
    assert rep["largest_cost_driver_step"] == "analyst" and rep["runs_with_model_calls"] == 1


def test_feedback_aggregation_by_prompt_version():
    feedback.record_feedback("r1", satisfactory=True, reason=None, client_id="c", prompt_versions={"analyst": "v2"})
    feedback.record_feedback("r2", satisfactory=False, reason="Wrong threshold", client_id="c", prompt_versions={"analyst": "v2"})
    s = feedback.summary()
    assert s["by_prompt_version"]["analyst:v2"] == {"n": 2, "satisfied": 1}
    assert s["top_reasons"][0][0] == "wrong threshold"
