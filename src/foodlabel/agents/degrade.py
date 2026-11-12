"""Fallback path when the model or verifier is unavailable (A5).

Produces a deterministic pre-check that is explicitly NOT a confident answer:
route is always regulatory-review, degraded=true, no compliance findings.
"""
from __future__ import annotations

from foodlabel.agents.claims import detect_claims
from foodlabel.common.schemas import Route, RunResult


def degraded_result(run_id: str, request: dict, reason: str, prompt_versions: dict | None = None) -> dict:
    claims = detect_claims(request)
    notes = []
    if claims:
        notes.append("claims detected on the label need condition checks: " + ", ".join(claims))
    if not request.get("allergen_statement"):
        notes.append("no allergen statement supplied")
    res = RunResult(
        run_id=run_id,
        route=Route.REGULATORY_REVIEW,
        reason=f"Degraded run: {reason}. No grounded compliance finding was produced; manual review required."
        + (" Pre-check: " + "; ".join(notes) + "." if notes else ""),
        findings=[],
        escalated=True,
        escalation_path=["agent run", f"degraded: {reason}", "regulatory-review"],
        degraded=True,
        claims_detected=claims,
        prompt_versions=prompt_versions or {},
    )
    return res.model_dump(mode="json")
