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


def _analyst(messages: list[dict]) -> dict:
    obs = _observations(messages)
    if len(obs) == 0:
        return {"action": "search_corpus", "args": {"query": "labelling requirements for this product", "doc": "labelling", "k": 3}}
    if len(obs) == 1:
        return {"action": "search_corpus", "args": {"query": "conditions for nutrient content claims", "doc": "claims", "k": 3}}
    passages = [p for o in obs for p in PASSAGE_RE.findall(o)]
    first_user = next((m["content"] for m in messages if m.get("role") == "user"), "")
    m = CLAIMS_RE.search(first_user)
    claims = [] if not m or m.group(1).strip().lower() == "none" else [c.strip() for c in m.group(1).split("|")]
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
    if label_passages:
        pid, _doc, _clause, text = label_passages[0]
        findings.append(
            {"requirement_id": "LBL-1", "claim": None, "summary": "Labelling requirement reviewed",
             "status": "met", "explanation": "Stub finding.",
             "citations": [{"passage_id": pid, "quote": text[:80].rsplit(" ", 1)[0]}], "values": None}
        )
    for i, claim in enumerate(claims, 1):
        if not claim_passages:
            break
        pid, _doc, _clause, text = claim_passages[0]
        quote = "invented text that appears in no retrieved passage" if bad_quote else text[:80].rsplit(" ", 1)[0]
        findings.append(
            {"requirement_id": f"CLAIM-{i}", "claim": claim, "summary": f"Condition for claim '{claim}'",
             "status": "needs_review", "explanation": "Stub finding.",
             "citations": [{"passage_id": pid, "quote": quote}], "values": None}
        )
    return {"action": "finish", "findings": findings, "conflicts": [], "possible_exemptions": []}


def _respond(step: str, messages: list[dict]) -> str:
    if step == "classify":
        return json.dumps({"in_scope": True, "category": "in-scope"})
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
