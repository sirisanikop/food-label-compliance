"""Detect claims printed on the label (F3).

A deliberately simple, inspectable lexicon. Extend CLAIM_PATTERNS for your category.
Claims declared explicitly in the request are always included.
"""
from __future__ import annotations

import re

CLAIM_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("nutrient_content", re.compile(r"\b(?:good\s+)?(?:source of|rich in|high in)\s+(?:protein|fibre|fiber|calcium|iron|vitamin\s*[a-z0-9]+|omega[- ]?3)\b", re.I)),
    ("nutrient_content", re.compile(r"\bhigh[- ]protein\b|\bhigh[- ]fibre\b|\bhigh[- ]fiber\b", re.I)),
    ("nutrient_content", re.compile(r"\b(?:low|reduced|light|lite)\s+(?:in\s+)?(?:fat|sugar|sugars|salt|sodium|cholesterol|calories?|energy)\b", re.I)),
    ("nutrient_content", re.compile(r"\b(?:fat|sugar|sugars|salt|sodium|cholesterol|trans[- ]fat|gluten)[- ]free\b", re.I)),
    ("nutrient_content", re.compile(r"\b(?:zero|no)\s+(?:added\s+)?(?:sugar|sugars|salt|preservatives?|trans fat)\b", re.I)),
    ("health", re.compile(r"\b(?:boosts?|improves?|supports?|strengthens?|promotes?)\s+(?:immunity|digestion|heart health|bone health|gut health)\b", re.I)),
    ("natural", re.compile(r"\b100%\s+natural\b|\bnatural\b|\bfresh\b|\bpure\b|\borganic\b", re.I)),
]


def detect_claims(request: dict) -> list[str]:
    found: list[str] = []
    for c in request.get("claims_declared") or []:
        c = c.strip().lower()
        if c and c not in found:
            found.append(c)
    text = " ".join(filter(None, [request.get("front_of_pack_text"), request.get("product_name")]))
    for _kind, pat in CLAIM_PATTERNS:
        for m in pat.finditer(text):
            phrase = re.sub(r"\s+", " ", m.group(0).strip().lower())
            if phrase not in found:
                found.append(phrase)
    return found
