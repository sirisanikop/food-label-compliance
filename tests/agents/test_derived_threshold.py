"""Thresholds the regulation states as a percentage of the RDA (e.g. 'source of protein': 10 per cent. of RDA)."""
from foodlabel.agents import tools
from foodlabel.common.reference import get_reference
from foodlabel.common.schemas import Citation, Finding, PassageRef
from foodlabel.retrieval import ids
from foodlabel.verifier import checks

TEXT = "11. Protein Source 10 per cent. of Recommended Dietary Allowance per 100 g for solids"


def passage():
    return PassageRef(passage_id=ids.make_passage_id("ADV", "Schedule I", TEXT), doc_id="ADV", doc_role="claims", clause_ref="Schedule I", text=TEXT)


def finding(p, *, declared=8.0, status="met", pct=10, ref=54.0, threshold=5.4, nutrient="protein"):
    v = {"declared": declared, "comparator": "gte", "threshold": threshold, "percent_of_reference": pct, "reference": {"nutrient": nutrient, "value": ref}}
    return Finding(requirement_id="CLAIM-1", claim="source of protein", status=status, explanation="x", values=v,
                   citations=[Citation(passage_id=p.passage_id, quote="10 per cent. of Recommended Dietary Allowance per 100 g for solids")])


def codes(f, p):
    issues, _ = checks.check_findings([f], [p], ["source of protein"])
    return {i.code for i in issues}


def test_correct_derivation_passes_both_ways():
    p = passage()
    assert codes(finding(p), p) == set()
    assert codes(finding(p, declared=2.5, status="unmet"), p) == set()


def test_wrong_arithmetic_is_caught():
    p = passage()
    assert "NUMERIC_MISMATCH" in codes(finding(p, threshold=6.0), p)


def test_percentage_must_be_in_the_cited_text():
    p = passage()
    assert "THRESHOLD_NOT_IN_QUOTE" in codes(finding(p, pct=20, threshold=10.8), p)


def test_invented_reference_value_is_rejected():
    p = passage()
    assert "REFERENCE_UNSUPPORTED" in codes(finding(p, ref=60.0, threshold=6.0), p)
    assert "REFERENCE_UNSUPPORTED" in codes(finding(p, nutrient="vitamin c"), p)


def test_status_must_follow_the_derived_comparison():
    p = passage()
    assert "NUMERIC_MISMATCH" in codes(finding(p, declared=2.5, status="met"), p)


def test_reference_table_and_tool():
    assert get_reference("Protein")["value"] == 54.0 and get_reference("vitamin c") is None
    ctx = tools.ToolContext(run_id="r", request={}, retriever=None, settings=__import__("foodlabel.common.config", fromlist=["x"]).get_settings())
    text, ok = tools.execute("get_reference_value", {"nutrient": "protein"}, ctx)
    assert ok and '"value": 54.0' in text and "ICMR" in text
    text, ok = tools.execute("get_reference_value", {"nutrient": "zinc"}, ctx)
    assert ok and '"found": false' in text
