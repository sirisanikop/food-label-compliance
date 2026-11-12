"""Evaluation metrics (L3). Each one's computation is stated here and in docs/evaluation.md.

These are deterministic equivalents of the TruLens feedback functions, so they are cheap, repeatable
and usable on live runs. docs/evaluation.md describes how to add TruLens LLM-judged versions beside them.

  groundedness        share of citations whose quote appears verbatim in the retrieved passage text
                      it cites (1.0 if there are no findings; 0.0 if findings exist but cite nothing)
  context_relevance   share of the EXPECTED supporting passages that the agent retrieved (recall)
  context_precision   share of CITED passages that are expected supporting passages
  faithfulness        share of findings carrying numeric `values` whose status agrees with the
                      comparison recomputed from those values (1.0 if none carry values)
  hallucination_rate  share of findings with at least one unsupported element: a citation that does
                      not resolve, a quote not in its passage, a met/unmet status with no citation,
                      or a stated threshold that does not appear in the cited quote
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

_NUM = re.compile(r"\d+(?:\.\d+)?")


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip().lower()


def _resolves(citation: dict, retrieved: dict) -> bool:
    p = retrieved.get(citation.get("passage_id"))
    return bool(p) and bool(citation.get("quote", "").strip()) and _norm(citation["quote"]) in _norm(p["text"])


def groundedness(findings: list[dict], retrieved: dict) -> float:
    cites = [c for f in findings for c in f.get("citations", [])]
    if not cites:
        return 1.0 if not findings else 0.0
    return sum(_resolves(c, retrieved) for c in cites) / len(cites)


def _status_from_values(v: dict) -> Optional[str]:
    try:
        d, t = float(v["declared"]), float(v["threshold"])
    except (KeyError, TypeError, ValueError):
        return None
    ok = {"gte": d >= t, "gt": d > t, "lte": d <= t, "lt": d < t, "eq": abs(d - t) < 1e-9}.get(v.get("comparator"))
    return None if ok is None else ("met" if ok else "unmet")


def faithfulness(findings: list[dict]) -> float:
    with_values = [f for f in findings if f.get("values")]
    if not with_values:
        return 1.0
    agree = 0
    for f in with_values:
        expected = _status_from_values(f["values"])
        agree += int(expected is not None and f.get("status") in (expected, "needs_review"))
    return agree / len(with_values)


def hallucination_rate(findings: list[dict], retrieved: dict) -> float:
    if not findings:
        return 0.0
    bad = 0
    for f in findings:
        cites = f.get("citations", [])
        unsupported = f.get("status") in ("met", "unmet") and not cites
        unsupported |= any(not _resolves(c, retrieved) for c in cites)
        v = f.get("values") or {}
        if "threshold" in v and cites:
            try:
                th = float(v["threshold"])
                nums = {float(n) for n in _NUM.findall(" ".join(c.get("quote", "") for c in cites))}
                unsupported |= not any(abs(n - th) < 1e-9 for n in nums)
            except (TypeError, ValueError):
                unsupported = True
        bad += int(unsupported)
    return bad / len(findings)


def _matches(passage: dict, spec: dict) -> bool:
    if spec.get("doc_role") and passage.get("doc_role") != spec["doc_role"]:
        return False
    sub = spec.get("clause_contains", "").lower()
    return sub in (passage.get("clause_ref", "") + " " + passage.get("text", "")).lower()


def context_relevance(retrieved: dict, expected: list[dict]) -> Optional[float]:
    if not expected:
        return None
    return sum(any(_matches(p, e) for p in retrieved.values()) for e in expected) / len(expected)


def context_precision(findings: list[dict], retrieved: dict, expected: list[dict]) -> Optional[float]:
    if not expected:
        return None
    cited = {c["passage_id"] for f in findings for c in f.get("citations", []) if c.get("passage_id") in retrieved}
    if not cited:
        return 0.0
    return sum(any(_matches(retrieved[pid], e) for e in expected) for pid in cited) / len(cited)


def mean(values: Iterable[Optional[float]]) -> Optional[float]:
    vs = [v for v in values if v is not None]
    return round(sum(vs) / len(vs), 4) if vs else None
