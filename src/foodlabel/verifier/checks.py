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
            if f.citations and not _num_in(quotes, threshold):
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
