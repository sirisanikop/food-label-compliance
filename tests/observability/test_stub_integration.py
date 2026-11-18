"""The stub model must drive the REAL agent graph to a verified answer, so that load tests exercise
the full pipeline (analyst loop, tools, verifier, routing) and not a shortcut."""
import importlib.util
import pathlib

from starlette.testclient import TestClient

from conftest import sample_request
from foodlabel.agents.graph import Deps
from foodlabel.agents.llm import LLMClient
from foodlabel.agents.runner import run_agent
from foodlabel.common.config import get_settings
from foodlabel.observability import trace_store
from foodlabel.common.schemas import VerifyResponse
from foodlabel.verifier import checks
from foodlabel.retrieval import ids

STUB = pathlib.Path(__file__).resolve().parents[2] / "services" / "stub_llm" / "app.py"
_L = "Every package of pre-packaged food shall carry the name of the food and the net quantity of the contents."
_C = "Schedule I Nutrient Content Claim\n11. Protein\nSource\n10 per cent. of Recommended Dietary Allowance per 100 g for solids\n5 per cent. of Recommended Dietary Allowance per 100 ml for liquids"
_P = {
    "labelling": [{"passage_id": ids.make_passage_id("LAB", "Reg 5", _L), "doc_id": "LAB", "doc_role": "labelling", "clause_ref": "Reg 5", "text": _L, "score": 0.9}],
    "claims": [{"passage_id": ids.make_passage_id("ADV", "Sch I", _C), "doc_id": "ADV", "doc_role": "claims", "clause_ref": "Sch I", "text": _C, "score": 0.9}],
}


class FakeRetriever:
    def search(self, query, doc=None, k=5):
        return _P.get(doc, _P["labelling"])

    def get_clause(self, cid):
        return []


def load_stub():
    spec = importlib.util.spec_from_file_location("stub_llm_app", STUB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.app


class InProc:
    def verify(self, req):
        issues, run = checks.check_findings(req.findings, req.retrieved, req.claims_detected)
        return VerifyResponse(ok=not issues, issues=issues, suggested_strategy=checks.suggest_strategy(issues), checks_run=run)


def _run(run_id, fault):
    import os

    if fault:
        os.environ["STUB_FAULT"] = fault
    try:
        return run_agent(run_id, sample_request(), Deps(llm=LLMClient(http=TestClient(load_stub())), retriever=FakeRetriever(), verifier=InProc(), settings=get_settings()))
    finally:
        os.environ.pop("STUB_FAULT", None)


def test_fault_wrong_source_once_triggers_a_further_search_then_verifies():
    out = _run("f1", "wrong_source_once")
    assert out.status == "completed" and not out.result["escalated"]
    v = [e for e in trace_store.get_trace("f1") if e["kind"] == "verification"]
    assert [e["ok"] for e in v] == [False, True]
    assert "CLAIM_CONDITION_MISSING" in str(v[0]["data"]["issues"])
    assert out.tool_calls == 4  # labelling search, claims search, RDA lookup + the verifier-directed claims search


def test_fault_bad_quote_once_is_caught_and_corrected():
    out = _run("f2", "bad_quote_once")
    v = [e for e in trace_store.get_trace("f2") if e["kind"] == "verification"]
    assert [e["ok"] for e in v] == [False, True]
    assert "QUOTE_NOT_IN_PASSAGE" in str(v[0]["data"]["issues"])
    assert out.result["findings"] and not out.result["escalated"]


def test_fault_always_bad_quote_escalates_instead_of_returning_unverified_answer():
    out = _run("f3", "always_bad_quote")
    r = out.result
    assert r["escalated"] and r["findings"] == [] and r["withheld_findings"] >= 1


def test_stub_completes_a_full_verified_run_with_costs_recorded(monkeypatch):
    monkeypatch.setenv("PRICE_IN_PER_1K", "0.01")
    from foodlabel.common import config

    config.reload_settings()
    try:
        http = TestClient(load_stub())
        llm = LLMClient(http=http)
        out = run_agent("stub-run", sample_request(), Deps(llm=llm, retriever=FakeRetriever(), verifier=InProc(), settings=get_settings()))
        r = out.result
        assert out.status == "completed" and not r["escalated"], r["reason"]
        assert r["findings"] and r["verification"]["ok"] is True
        s = trace_store.summarize("stub-run")
        assert s["llm_calls"] >= 4 and s["tool_calls"] == 3
        assert set(s["per_step"]) == {"classify", "analyst"}
        assert s["cost_usd"] > 0 and s["prompt_tokens"] > 0
    finally:
        monkeypatch.delenv("PRICE_IN_PER_1K")
        config.reload_settings()


def test_stub_protein_claim_is_decided_with_the_derived_rda_threshold():
    """8 g meets 10% of the 54 g RDA (5.4 g); 2.5 g does not. The verifier must accept the derived threshold."""
    ok = run_agent("p-ok", sample_request(), Deps(llm=LLMClient(http=TestClient(load_stub())), retriever=FakeRetriever(), verifier=InProc(), settings=get_settings()))
    assert ok.result["route"] == "clear-for-print"
    claim = next(f for f in ok.result["findings"] if f["claim"])
    assert claim["status"] == "met" and claim["values"]["threshold"] == 5.4 and claim["values"]["percent_of_reference"] == 10
    low = sample_request()
    low["nutrition"] = {**low["nutrition"], "protein_g": 2.5, "carbohydrate_g": 62}
    bad = run_agent("p-low", low, Deps(llm=LLMClient(http=TestClient(load_stub())), retriever=FakeRetriever(), verifier=InProc(), settings=get_settings()))
    assert bad.result["route"] == "revise-and-recheck"
    assert next(f for f in bad.result["findings"] if f["claim"])["status"] == "unmet"


def test_stub_rejects_products_outside_the_category():
    req = sample_request(product_name="Cold Pressed Orange Juice", category_hint="Fruit juice", ingredients_text="Orange juice")
    out = run_agent("oos", req, Deps(llm=LLMClient(http=TestClient(load_stub())), retriever=FakeRetriever(), verifier=InProc(), settings=get_settings()))
    assert out.result["route"] == "regulatory-review" and "outside the corpus scope" in out.result["reason"]
