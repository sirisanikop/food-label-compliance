from foodlabel.retrieval import chunker, ids

LABELLING = """CHAPTER II
GENERAL REQUIREMENTS
5. Labelling of pre-packaged foods
(1) Every package shall carry the name of the food.
(2) The net quantity shall be declared.
6. Nutritional information
(1) Every package shall carry a nutrition panel. See Schedule II.
"""

CLAIMS = """SCHEDULE I
CONDITIONS FOR NUTRIENT CONTENT CLAIMS
Source of protein   not less than 6 g per 100 g
High protein        not less than 12 g per 100 g
Low fat             not more than 3 g per 100 g
""" + "\n".join(f"Extra row {i}   value {i}" for i in range(120))


def chunks(text, role="labelling", max_chars=400):
    return chunker.chunk_document([text], doc_id="DOC", doc_role=role, doc_title="Test Regs", max_chars=max_chars)


def test_regulations_become_separate_clauses():
    refs = [c.clause_ref for c in chunks(LABELLING)]
    assert "Reg 5" in refs and "Reg 6" in refs


def test_embedded_text_carries_context_but_stored_text_is_raw():
    c = next(c for c in chunks(LABELLING) if c.clause_ref == "Reg 5")
    assert c.embed_text.startswith("Test Regs | ") and "Reg 5" in c.embed_text.splitlines()[0]
    assert not c.text.startswith("Test Regs")
    assert "shall carry the name of the food" in c.text


def test_schedule_heading_repeats_in_every_chunk_so_rows_stay_tied_to_it():
    cs = [c for c in chunks(CLAIMS, role="claims", max_chars=300) if c.clause_ref.upper().startswith("SCHEDULE")]
    assert len(cs) > 2
    assert all(c.text.splitlines()[0].upper().startswith("SCHEDULE I") for c in cs)
    assert any("Source of protein" in c.text for c in cs)


def test_cross_references_are_recorded_as_mentions():
    c = next(c for c in chunks(LABELLING) if c.clause_ref == "Reg 6")
    assert "schedule ii" in c.mentions


def test_passage_id_binds_id_to_text():
    c = chunks(LABELLING)[0]
    assert ids.verify_passage_id(c.passage_id, c.text)
    assert not ids.verify_passage_id(c.passage_id, c.text + " altered")
    assert ":" not in ids.slug("Reg 5 (1)") and '"' not in c.passage_id


def test_chunking_is_deterministic_for_idempotent_ingestion():
    assert [c.passage_id for c in chunks(LABELLING)] == [c.passage_id for c in chunks(LABELLING)]
