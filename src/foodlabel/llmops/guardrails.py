"""Guardrails (L4): prompt-injection defence, output filtering, toxicity detection.

Input side (check_request) runs on EVERY free-text field before any model sees it:
  * strips invisible/control characters (hidden-text injection)
  * flags instruction-override / role-hijack / route-forcing / exfiltration patterns
  * HIGH severity or toxic content -> blocked: the run escalates without any model call
  * MEDIUM severity -> delimiter characters neutralised, run continues, flag recorded
  * label data is always presented to models inside <label_data> tags as untrusted DATA

Output side (filter_output) guarantees the service never issues a determination it is not
authorised to make: no "approved for print", route is one of the three allowed values, and a
disclaimer is always present.

This is pattern-based and deliberately inspectable. It is a first layer, not a proof of safety:
extend PATTERNS from your own red-team cases, and swap toxicity_score for a model if you have one.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from foodlabel.common.schemas import Route
from foodlabel.observability import metrics

FREE_TEXT_FIELDS = (
    "product_name", "category_hint", "ingredients_text", "allergen_statement",
    "front_of_pack_text", "label_text",
)

HIGH_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("override_instructions", re.compile(r"(?:ignore|disregard|forget|override|bypass)\s+(?:all|any|the|your|previous|prior|above|earlier|these)[\w\s,]{0,30}(?:instructions?|rules?|prompts?|guidelines?|constraints?)", re.I)),
    ("role_hijack", re.compile(r"\byou are now\b|\bact as (?:the|an?)\b|\bnew (?:system )?instructions?\b|\bpretend (?:to be|you are)\b|\bfrom now on\b", re.I)),
    ("route_forcing", re.compile(r"(?:mark|set|route|classify|declare|treat|approve|output)\b[\w\s,\-]{0,40}(?:clear[- ]for[- ]print|approved|fully compliant|as compliant)", re.I)),
    ("prompt_exfiltration", re.compile(r"(?:reveal|print|show|repeat|leak|output)\b[\w\s]{0,30}(?:system prompt|your instructions|hidden prompt)", re.I)),
    ("chat_markup", re.compile(r"<\|?(?:im_start|im_end|system|assistant)\|?>|\[/?INST\]|###\s*(?:system|instruction)", re.I)),
]
MEDIUM_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("tool_abuse", re.compile(r"\b(?:call|invoke|use|run) the (?:tool|function|search|compute)\b", re.I)),
    ("delimiter_break", re.compile(r"</?\s*(?:label_data|passage|field)\b[^>]*>", re.I)),
]
_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤﻿\x00-\x08\x0b\x0c\x0e-\x1f]")

_TOXIC_TERMS = (
    "idiot", "stupid", "moron", "shut up", "damn", "crap", "bastard", "hate you",
    "kill you", "i will hurt", "go to hell",
)
_TOXIC_RE = re.compile("|".join(re.escape(t) for t in _TOXIC_TERMS), re.I)
TOXICITY_THRESHOLD = 0.5


def toxicity_score(text: str) -> float:
    """Crude lexicon score in [0,1]. Replace with a classifier if available."""
    hits = len(_TOXIC_RE.findall(text or ""))
    return min(1.0, hits / 2.0)


@dataclass
class GuardrailReport:
    blocked: bool = False
    flags: list[dict] = field(default_factory=list)
    sanitized_request: dict = field(default_factory=dict)

    @property
    def summary(self) -> str:
        kinds = sorted({f["kind"] for f in self.flags})
        return ", ".join(kinds) if kinds else "none"


def _neutralise(text: str) -> str:
    return re.sub(r"<(/?)\s*(label_data|passage|field)", r"‹\1\2", text, flags=re.I)


def scan_text(text: str, field_name: str) -> list[dict]:
    flags: list[dict] = []
    for kind, pat in HIGH_PATTERNS:
        if pat.search(text):
            flags.append({"field": field_name, "kind": kind, "severity": "high"})
    for kind, pat in MEDIUM_PATTERNS:
        if pat.search(text):
            flags.append({"field": field_name, "kind": kind, "severity": "medium"})
    if toxicity_score(text) >= TOXICITY_THRESHOLD:
        flags.append({"field": field_name, "kind": "toxicity", "severity": "high"})
    return flags


def check_request(request: dict) -> GuardrailReport:
    report = GuardrailReport(sanitized_request=dict(request))
    items: list[tuple[str, str, Any]] = [(f, f, None) for f in FREE_TEXT_FIELDS if request.get(f)]
    items += [(f"claims_declared[{i}]", "claims_declared", i) for i, _ in enumerate(request.get("claims_declared") or [])]
    for label, key, idx in items:
        raw = request[key] if idx is None else request[key][idx]
        text = unicodedata.normalize("NFKC", raw)
        if _INVISIBLE.search(text):
            report.flags.append({"field": label, "kind": "hidden_characters", "severity": "medium"})
            text = _INVISIBLE.sub("", text)
        found = scan_text(text, label)
        report.flags += found
        text = _neutralise(text)
        if idx is None:
            report.sanitized_request[key] = text
        else:
            if report.sanitized_request[key] is request[key]:
                report.sanitized_request[key] = list(request[key])
            report.sanitized_request[key][idx] = text
    report.blocked = any(f["severity"] == "high" for f in report.flags)
    for f in report.flags:
        metrics.GUARDRAIL_EVENTS.labels(f["kind"]).inc()
    return report


# --------------------------------------------------------------------------- output
FORBIDDEN_OUTPUT = [
    re.compile(p, re.I)
    for p in (
        r"\bapproved for print\b", r"\bhereby approve[sd]?\b", r"\bi approve\b",
        r"\b(?:fully|100%|legally|completely) compliant\b", r"\bguarantee[sd]?\b",
        r"\bsafe to print\b", r"\bno further review (?:is )?(?:needed|required)\b",
    )
]
WITHHELD = "[determination withheld: the service cannot approve labels]"
DISCLAIMER = (
    "This is a compliance finding with supporting evidence, not an approval. "
    "A regulatory affairs professional makes the print decision."
)


def _clean(text: str, where: str, flags: list[dict]) -> str:
    out = text
    for pat in FORBIDDEN_OUTPUT:
        if pat.search(out):
            flags.append({"kind": "forbidden_determination", "where": where})
            out = pat.sub(WITHHELD, out)
    if toxicity_score(out) >= TOXICITY_THRESHOLD:
        flags.append({"kind": "toxic_output", "where": where})
        out = "[content withheld]"
    return out


def filter_output(result: dict) -> tuple[dict, list[dict]]:
    flags: list[dict] = []
    allowed = {r.value for r in Route}
    if result.get("route") not in allowed:
        flags.append({"kind": "invalid_route", "where": "route"})
        result["route"] = Route.REGULATORY_REVIEW.value
        result["escalated"] = True
    result["reason"] = _clean(result.get("reason", ""), "reason", flags)
    for i, f in enumerate(result.get("findings", [])):
        for key in ("summary", "explanation"):
            if f.get(key):
                f[key] = _clean(f[key], f"findings[{i}].{key}", flags)
    result["disclaimer"] = DISCLAIMER
    for f in flags:
        metrics.GUARDRAIL_EVENTS.labels(f["kind"]).inc()
    return result, flags
