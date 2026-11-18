"""Stub model provider for load tests and zero-cost development (D6).

Speaks the OpenAI-compatible /v1/chat/completions interface and returns fixed,
protocol-correct responses after a realistic delay (STUB_DELAY_MS, with jitter), so a
load test measures this system rather than a provider's rate limits.

It is tuned to the agent protocol in prompts/ and src/foodlabel/agents/graph.py:
  X-Agent-Step: classify | analyst | verifier_judge
The analyst stub performs one corpus search, one claims-scoped search, then finishes with
one finding per detected claim, citing the first passage it was shown.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="llm-stub")
DELAY_MS = float(os.environ.get("STUB_DELAY_MS", "800"))

PASSAGE_RE = re.compile(r'<passage id="([^"]+)" doc="([^"]*)" clause="([^"]*)"[^>]*>\s*(.*?)\s*</passage>', re.S)
CLAIMS_RE = re.compile(r"DETECTED_CLAIMS:\s*(.*)")


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _observations(messages: list[dict]) -> list[str]:
    return [m["content"] for m in messages if m.get("role") == "user" and m["content"].startswith("OBSERVATION")]


def _label(messages: list[dict]) -> dict:
    first_user = next((m["content"] for m in messages if m.get("role") == "user"), "")
    m = re.search(r"<label_data>\s*(\{.*\})\s*</label_data>", first_user, re.S)
    try:
        return json.loads(m.group(1)) if m else {}
    except ValueError:
        return {}


def _claims(messages: list[dict]) -> list[str]:
    first_user = next((m["content"] for m in messages if m.get("role") == "user"), "")
    m = CLAIMS_RE.search(first_user)
    return [] if not m or m.group(1).strip().lower() == "none" else [c.strip() for c in m.group(1).split("|")]


def _analyst(messages: list[dict]) -> dict:
    """A tiny scripted analyst that follows the real protocol: search labelling, search claims, look up the
    reference allowance for a protein claim, then finish. Only the protein claim is evaluated (10 per cent.
    of RDA, Schedule I row 11); other claims are left for a person (needs_review)."""
    obs = _observations(messages)
    claims = _claims(messages)
    protein = next((c for c in claims if "protein" in c), None)
    plan = [
        {"action": "search_corpus", "args": {"query": "labelling requirements for this product", "doc": "labelling", "k": 3}},
        {"action": "search_corpus", "args": {"query": (protein or (claims[0] if claims else "nutrient content claims")) + " conditions", "doc": "claims", "k": 4}},
    ]
    if protein:
        plan.append({"action": "get_reference_value", "args": {"nutrient": "protein"}})
    if len(obs) < len(plan):
        return plan[len(obs)]
    passages = [p for o in obs for p in PASSAGE_RE.findall(o)]
    label = _label(messages)
    findings = []
    claim_passages = [p for p in passages if p[1] == "claims"] or passages
    label_passages = [p for p in passages if p[1] == "labelling"] or passages
    # Fault injection for the demonstration (STUB_FAULT), see docs/runbook.md:
    #   wrong_source_once : first answer cites the labelling regulation for a claim (verifier asks for a claims search)
    #   bad_quote_once    : first answer quotes text that is not in the passage
    #   always_bad_quote  : every answer quotes invented text (run ends in escalation)
    fault = os.environ.get("STUB_FAULT", "")
    after_feedback = any("VERIFIER FEEDBACK" in x.get("content", "") for x in messages if x.get("role") == "user")
    if fault == "wrong_source_once" and not after_feedback:
        claim_passages = label_passages
    bad_quote = fault == "always_bad_quote" or (fault == "bad_quote_once" and not after_feedback)

    def quote_of(text: str, prefer: str | None = None) -> str:
        if prefer and prefer in text:
            return prefer
        flat = re.sub(r"\s+", " ", text)
        return flat[:90].rsplit(" ", 1)[0]

    if label_passages:
        pid, _doc, _clause, text = label_passages[0]
        findings.append(
            {"requirement_id": "LBL-1", "claim": None, "summary": "Mandatory label declarations", "status": "met",
             "explanation": "The label supplies the declarations this provision lists (stub model: not assessed in depth).",
             "citations": [{"passage_id": pid, "quote": quote_of(text)}], "values": None})
    for i, claim in enumerate(claims, 1):
        if not claim_passages:
            break
        is_protein = claim == protein
        pick = next((p for p in claim_passages if is_protein and re.search(r"11\.\s*Protein", p[3])), claim_passages[0])
        pid, _doc, _clause, text = pick
        quote = "invented text that appears in no retrieved passage" if bad_quote else quote_of(
            text, "10 per cent. of Recommended Dietary Allowance per 100 g for solids" if is_protein else None)
        f = {"requirement_id": f"CLAIM-{i}", "claim": claim, "summary": f"Conditions for the claim '{claim}'",
             "status": "needs_review", "explanation": "The stub model does not evaluate this claim; a person should.",
             "citations": [{"passage_id": pid, "quote": quote}], "values": None}
        if is_protein and re.search(r"11\.\s*Protein", text) and not bad_quote:
            declared = float((label.get("nutrition") or {}).get("protein_g", 0))
            threshold = round(0.10 * 54.0, 3)
            ok = declared >= threshold
            f.update(status="met" if ok else "unmet",
                     explanation=f"A 'source of protein' claim needs at least 10 per cent. of the protein allowance per 100 g of solid food. "
                                 f"The label declares {declared:g} g per 100 g against {threshold:g} g needed.",
                     values={"declared": declared, "comparator": "gte", "threshold": threshold, "unit": "g/100g",
                             "percent_of_reference": 10, "reference": {"nutrient": "protein", "value": 54.0}})
        findings.append(f)
    return {"action": "finish", "findings": findings, "conflicts": [], "possible_exemptions": []}


def _respond(step: str, messages: list[dict]) -> str:
    if step == "classify":
        user = next((m["content"] for m in messages if m.get("role") == "user"), "")
        cat = re.search(r'category "([^"]+)"', user)
        words = {w for w in re.findall(r"[a-z]{4,}", (cat.group(1) if cat else "").lower())}
        hint = re.search(r'"(?:category_hint|product_name)": "([^"]*)"', user)
        data = {}
        try:
            data = json.loads(re.search(r"<label_data>\s*(\{.*\})\s*</label_data>", user, re.S).group(1))
        except (AttributeError, ValueError):
            pass
        values = " ".join(str(data.get(k, "")) for k in ("product_name", "category_hint", "ingredients_text"))
        label_words = {w.rstrip("s") for w in re.findall(r"[a-z]{4,}", values.lower())}
        words = {w.rstrip("s") for w in words}
        in_scope = not words or bool(words & label_words)
        return json.dumps({"in_scope": in_scope, "category": hint.group(1) if hint else "unknown"})
    if step == "verifier_judge":
        return json.dumps({"supported": True, "reason": "stub"})
    return json.dumps(_analyst(messages))


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    step = request.headers.get("x-agent-step", "analyst")
    await asyncio.sleep(max(0.0, DELAY_MS * random.uniform(0.8, 1.2)) / 1000.0)
    if os.environ.get("STUB_FAULT") == "http_500":  # provider outage, for the circuit-breaker demonstration
        return JSONResponse(status_code=500, content={"error": "stub outage"})
    messages = body.get("messages", [])
    content = _respond(step, messages)
    prompt_text = " ".join(m.get("content", "") for m in messages)
    return {
        "id": "stub",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": _tokens(prompt_text), "completion_tokens": _tokens(content),
                  "total_tokens": _tokens(prompt_text) + _tokens(content)},
    }


@app.get("/health")
def health():
    return {"status": "ok", "delay_ms": DELAY_MS}
