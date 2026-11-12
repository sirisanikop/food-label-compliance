"""Routing decision (F6) and escalation rules (F7). Deterministic on purpose: the model
proposes findings; the route is derived from verified findings by code, not by the model.
"""
from __future__ import annotations

from foodlabel.common.schemas import Finding, Route


def decide_route(findings: list[Finding], escalations: list[str]) -> tuple[Route, str]:
    if escalations:
        return Route.REGULATORY_REVIEW, "Escalated to a human: " + "; ".join(escalations)
    unmet = [f for f in findings if f.status == "unmet"]
    if unmet:
        ids = ", ".join(f.requirement_id for f in unmet)
        return Route.REVISE_AND_RECHECK, f"Unmet requirement(s): {ids}. Revise the label and recheck."
    review = [f for f in findings if f.status == "needs_review"]
    if review:
        ids = ", ".join(f.requirement_id for f in review)
        return Route.REGULATORY_REVIEW, f"Evidence insufficient to decide: {ids}."
    if not findings:
        return Route.REGULATORY_REVIEW, "No verified findings were produced."
    return Route.CLEAR_FOR_PRINT, "All assessed requirements are met on the cited provisions. A professional decides."
