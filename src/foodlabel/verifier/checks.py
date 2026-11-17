"""Deterministic verification of an analyst draft against the passages it retrieved (A3).

The hard gate: no LLM involved. A finding passes only if every citation resolves to retrieved
text and the numbers it states are consistent with that text.
"""
from __future__ import annotations

import re
from typing import Optional

from foodlabel.common.schemas import Finding, PassageRef, VerifyIssue
from foodlabel.retrieval import ids

_NUM = re.compile(r"\d+(?:\.\d+)?")
CLAIMS_ROLE = "claims"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _compare(declared: float, comparator: str, threshold: float) -> Optional[bool]:
    return {
        "gte": declared >= threshold, "gt": declared > threshold,
        "lte": declared <= threshold, "lt": declared < threshold,
        "eq": abs(declared - threshold) < 1e-9,
    }.get(comparator)


def _num_in(text: str, value: float) -> bool:
    nums = {float(n) for n in _NUM.findall(text)}
    return any(abs(n - value) < 1e-9 for n in nums)


def _check_derived(i: int, v: dict, threshold: float, quotes: str, has_citation: bool) -> list[VerifyIssue]:
    """A threshold stated by the regulation as a percentage of a reference value (RDA).

    The percentage must be in the cited text, the reference must match the sourced table, and the
    threshold must equal percentage x reference. This keeps the arithmetic checkable instead of trusted.
    """
    from foodlabel.common.reference import get_reference

    out: list[VerifyIssue] = []
    try:
        pct = float(v["percent_of_reference"])
        ref = v.get("reference") or {}
        ref_value = float(ref["value"])
    except (KeyError, TypeError, ValueError):
        return [VerifyIssue(code="DERIVATION_INVALID", finding_index=i, detail="percent_of_reference needs reference.value and reference.nutrient")]
    if has_citation and not _num_in(quotes, pct):
        out.append(VerifyIssue(code="THRESHOLD_NOT_IN_QUOTE", finding_index=i, detail=f"{pct} per cent is not in the cited text"))
    known = get_reference(str(ref.get("nutrient", "")))
    if known is None or abs(known["value"] - ref_value) > 1e-9:
        out.append(VerifyIssue(code="REFERENCE_UNSUPPORTED", finding_index=i, detail=f"no sourced reference value {ref_value} for {ref.get('nutrient')}"))
    if abs(pct / 100.0 * ref_value - threshold) > 1e-6:
        out.append(VerifyIssue(code="NUMERIC_MISMATCH", finding_index=i, detail=f"{pct}% of {ref_value} is {pct / 100.0 * ref_value}, not {threshold}"))
    return out


def check_findings(
    findings: list[Finding], retrieved: list[PassageRef], claims_detected: list[str]
) -> tuple[list[VerifyIssue], list[str]]:
    by_id = {p.passage_id: p for p in retrieved}
    issues: list[VerifyIssue] = []
    checks = ["citation_resolves", "passage_integrity", "quote_verbatim", "citation_present",
              "claim_coverage", "numeric_consistency"]

    for i, f in enumerate(findings):
        if f.status in ("met", "unmet") and not f.citations:
            issues.append(VerifyIssue(code="NO_CITATION", finding_index=i, detail=f.requirement_id))
        for c in f.citations:
            p = by_id.get(c.passage_id)
            if p is None:
                issues.append(VerifyIssue(code="CITATION_NOT_RETRIEVED", finding_index=i, detail=c.passage_id))
                continue
            if not ids.verify_passage_id(p.passage_id, p.text):
                issues.append(VerifyIssue(code="PASSAGE_TAMPERED", finding_index=i, detail=c.passage_id))
            if not c.quote.strip() or _norm(c.quote) not in _norm(p.text):
                issues.append(VerifyIssue(code="QUOTE_NOT_IN_PASSAGE", finding_index=i, detail=c.passage_id))
        v = f.values or {}
        if {"declared", "comparator", "threshold"} <= v.keys():
            try:
                declared, threshold = float(v["declared"]), float(v["threshold"])
            except (TypeError, ValueError):
                issues.append(VerifyIssue(code="NUMERIC_MISMATCH", finding_index=i, detail="non-numeric values"))
                continue
            holds = _compare(declared, str(v["comparator"]), threshold)
            expected = "met" if holds else "unmet"
            if holds is None or (f.status in ("met", "unmet") and f.status != expected):
                issues.append(VerifyIssue(code="NUMERIC_MISMATCH", finding_index=i,
                                          detail=f"{declared} {v['comparator']} {threshold} is {holds}, status says {f.status}"))
            quotes = " ".join(c.quote for c in f.citations)
            derived = v.get("percent_of_reference")
            if derived is not None:
                issues += _check_derived(i, v, float(threshold), quotes, bool(f.citations))
            elif f.citations and not _num_in(quotes, threshold):
                issues.append(VerifyIssue(code="THRESHOLD_NOT_IN_QUOTE", finding_index=i, detail=str(threshold)))

    # Every detected claim needs a finding that cites the claims regulation (the case in section 4.1).
    for claim in claims_detected:
        covered = False
        for f in findings:
            if (f.claim or "").strip().lower() == claim.strip().lower() or claim.lower() in (f.claim or "").lower():
                if any(by_id.get(c.passage_id) and by_id[c.passage_id].doc_role == CLAIMS_ROLE for c in f.citations):
                    covered = True
                    break
        if not covered:
            issues.append(VerifyIssue(code="CLAIM_CONDITION_MISSING", finding_index=None, detail=claim))

    return issues, checks


def suggest_strategy(issues: list[VerifyIssue]) -> Optional[str]:
    codes = {i.code for i in issues}
    if "CLAIM_CONDITION_MISSING" in codes:
        return "search_claims_regulation"
    if codes & {"CITATION_NOT_RETRIEVED", "QUOTE_NOT_IN_PASSAGE", "PASSAGE_TAMPERED"}:
        return "re_retrieve_by_clause"
    if codes:
        return "recompute_and_recite"
    return None
