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


# --- regressions found on the real FSSAI compendium PDFs -------------------------------------------

COMPENDIUM = """Version-IV (14.12.2022)
9. Conditional Claims.- (1) a claim may be made where a food is by its nature high or low.
10. Prohibited claims.- (1) no claims shall be made which refer to suitability.
11.Approval of claims. - The food business operator shall seek prior approval.
12. Procedure for approval of claims. - (1) The operator shall submit an application.
Version-IV (14.12.2022)
The conditions are as given in
Schedule II of these regulations.
3[SCHEDULE – I
[See regulation 5 (3) and regulation 7 (1)(b)]
Nutrient Content Claim
1.
Energy Low Not more than 40 kcal per 100 g
2. Fat Low Not more than 3 g of fat per 100 g
Free Not more than 0.5 g of fat per 100 g
11. Protein
Source
10 per cent. of Recommended Dietary Allowance per 100 g for solids
Rich /
High
20 per cent. of Recommended Dietary Allowance per 100 g for solids
12. Vitamin(s) or Mineral(s)
Source 15 per cent. of Recommended Dietary Allowance
"""


def _compendium():
    return chunker.chunk_document([COMPENDIUM], doc_id="CL", doc_role="claims", doc_title="Claims", max_chars=1400)


def test_table_rows_are_not_mistaken_for_regulations():
    regs = [c.clause_ref for c in _compendium() if c.clause_ref.startswith("Reg ")]
    assert regs == ["Reg 9", "Reg 10", "Reg 11", "Reg 12"]  # no second "Reg 11"/"Reg 12" from the table


def test_heading_with_no_space_after_the_dot_is_a_regulation():
    assert "Reg 11" in [c.clause_ref for c in _compendium()]


def test_sentence_that_starts_with_schedule_is_not_a_heading():
    cs = _compendium()
    assert [c.clause_ref for c in cs if c.clause_ref.startswith("Schedule")] == ["Schedule I"] * len(
        [c for c in cs if c.clause_ref.startswith("Schedule")]
    )
    assert any("Schedule II of these regulations" in c.text for c in cs if c.clause_ref == "Reg 12")


def test_schedule_rows_are_separate_passages_with_the_row_in_the_path():
    protein = [c for c in _compendium() if "Row 11" in c.section_path]
    assert len(protein) == 1
    p = protein[0]
    assert p.clause_ref == "Schedule I" and "Protein" in p.section_path
    assert p.text.splitlines()[0].startswith("Schedule I [See regulation 5 (3)")
    assert "10 per cent." in p.text and "20 per cent." in p.text
    assert "Vitamin" not in p.text and "Fat" not in p.text


def test_running_page_header_is_dropped():
    assert not any("Version-IV" in c.text for c in _compendium())
