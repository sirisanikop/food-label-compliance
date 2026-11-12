from foodlabel.common.schemas import Citation, Finding, PassageRef
from foodlabel.retrieval import ids
from foodlabel.verifier import checks

TEXT = "Source of protein: not less than 6 g of protein per 100 g of the food."


def passage(role="claims", text=TEXT):
    return PassageRef(passage_id=ids.make_passage_id("DOC", "Sch 1", text), doc_id="DOC", doc_role=role, clause_ref="Sch 1", text=text)


def finding(p, quote=None, status="met", values=None, claim="source of protein"):
    return Finding(requirement_id="CLAIM-1", claim=claim, status=status, explanation="x", values=values,
                   citations=[Citation(passage_id=p.passage_id, quote=quote or "not less than 6 g of protein")])


def codes(findings, retrieved, claims=("source of protein",)):
    issues, _ = checks.check_findings(findings, retrieved, list(claims))
    return {i.code for i in issues}


def test_valid_finding_passes():
    p = passage()
    v = {"declared": 8.0, "comparator": "gte", "threshold": 6.0}
    assert codes([finding(p, values=v)], [p]) == set()


def test_citation_must_resolve_to_retrieved_passage():
    p = passage()
    f = finding(p)
    f.citations[0].passage_id = "DOC:other:abc"
    assert "CITATION_NOT_RETRIEVED" in codes([f], [p])


def test_quote_must_be_verbatim():
    p = passage()
    assert "QUOTE_NOT_IN_PASSAGE" in codes([finding(p, quote="at least ten grams of protein")], [p])


def test_tampered_passage_text_is_detected():
    p = passage()
    tampered = PassageRef(**{**p.model_dump(), "text": TEXT.replace("6 g", "1 g")})
    assert "PASSAGE_TAMPERED" in codes([finding(tampered, quote="not less than 1 g")], [tampered])


def test_claim_without_claims_regulation_citation_is_flagged():
    lab = passage(role="labelling", text="Every package shall carry the name of the food.")
    f = finding(lab, quote="shall carry the name of the food")
    assert "CLAIM_CONDITION_MISSING" in codes([f], [lab])


def test_numeric_status_must_match_recomputation():
    p = passage()
    v = {"declared": 2.5, "comparator": "gte", "threshold": 6.0}
    assert "NUMERIC_MISMATCH" in codes([finding(p, status="met", values=v)], [p])
    assert "NUMERIC_MISMATCH" not in codes([finding(p, status="unmet", values=v)], [p])


def test_threshold_must_appear_in_quote():
    p = passage()
    v = {"declared": 8.0, "comparator": "gte", "threshold": 10.0}
    assert "THRESHOLD_NOT_IN_QUOTE" in codes([finding(p, values=v)], [p])


def test_met_without_citation_is_flagged():
    f = Finding(requirement_id="R", status="met", explanation="x", citations=[])
    assert "NO_CITATION" in codes([f], [], claims=())


def test_strategy_suggestion():
    p = passage(role="labelling")
    issues, _ = checks.check_findings([finding(p)], [p], ["source of protein"])
    assert checks.suggest_strategy(issues) == "search_claims_regulation"
