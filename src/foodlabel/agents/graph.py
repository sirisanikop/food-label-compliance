"""LangGraph reasoning layer (A1) for the analyst agent, with hand-off to the verifier agent (A2).

    guard -> classify -> analyst <-> (tool calls) -> verify -> finalize
                            ^                           |
                            +---- different strategy ---+   (bounded by MAX_VERIFY_ATTEMPTS)

* The analyst model chooses its own actions each turn (search / fetch / read field / compute / finish).
  Retrieval is a tool it calls, not a fixed stage.
* The loop is bounded by steps, tool calls and wall-clock time.
* The verifier is a separate agent (own service). Nothing unverified is returned: on failure the
  graph searches again with a different strategy, and if that fails it escalates.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import ValidationError

from foodlabel.agents import router, tools
from foodlabel.agents.claims import detect_claims
from foodlabel.agents.jsonutil import extract_json
from foodlabel.agents.verifier_client import VerifierUnavailable
from foodlabel.common.config import Settings
from foodlabel.common.schemas import Finding, PassageRef, RunResult, VerifyRequest
from foodlabel.llmops import costs, guardrails, prompts
from foodlabel.observability import metrics, trace_store
from foodlabel.observability.tracing import span

log = logging.getLogger("foodlabel.graph")


class AgentState(TypedDict, total=False):
    run_id: str
    request: dict
    claims: list
    messages: list
    retrieved: dict
    steps: int
    tool_calls: int
    attempt: int
    draft: Optional[dict]
    findings: list
    verification: Optional[dict]
    escalations: list
    guardrail_flags: list
    degraded: bool
    phase: str
    limit_hit: Optional[str]
    deadline: float
    prompt_versions: dict
    result: dict


@dataclass
class Deps:
    llm: Any  # .chat(messages, step=, run_id=, prompt_name=, prompt_version=) -> LLMResponse
    retriever: Any  # .search(query, doc=, k=) / .get_clause(id)
    verifier: Any  # .verify(VerifyRequest) -> VerifyResponse
    settings: Settings
    get_prompt: Callable = prompts.get_prompt


STRATEGY_HINTS = {
    "CLAIM_CONDITION_MISSING": (
        "A claim on the label has no condition cited from the claims regulation. Search the claims "
        "regulation (doc='claims') for the conditions attached to that claim, read them, compare with "
        "the declared values using compute, and cite the condition."
    ),
    "CITATION_NOT_RETRIEVED": "Cite only passage ids that appear in your OBSERVATIONS. Re-retrieve the provision with get_clause.",
    "QUOTE_NOT_IN_PASSAGE": "Quotes must be copied verbatim from the passage text. Re-read the passage and quote it exactly.",
    "NO_CITATION": "Every met/unmet finding needs at least one citation with a verbatim quote.",
    "NUMERIC_MISMATCH": "Recompute the comparison with the compute tool and correct the status.",
    "THRESHOLD_NOT_IN_QUOTE": "The threshold you state must appear in the quoted text. Quote the sentence containing it.",
    "NOT_ENTAILED": "The cited passage does not support the explanation. Find a passage that does, or set status needs_review.",
}


def _label_data(request: dict) -> str:
    data = dict(request)
    if data.get("label_text") and len(data["label_text"]) > 3000:
        data["label_text"] = data["label_text"][:3000] + " ..."
    return json.dumps(data, indent=1, default=str)


def _build_state(run_id: str, request: dict, settings: Settings, prompt_versions: dict) -> AgentState:
    return AgentState(
        run_id=run_id, request=request, claims=[], messages=[], retrieved={}, steps=0, tool_calls=0,
        attempt=0, draft=None, findings=[], verification=None, escalations=[], guardrail_flags=[],
        degraded=False, phase="guard", limit_hit=None, deadline=time.time() + settings.run_timeout_s,
        prompt_versions=prompt_versions,
    )


def build_graph(deps: Deps):
    s = deps.settings

    def timed(step: str, t0: float) -> float:
        dt = time.perf_counter() - t0
        metrics.STEP_DURATION.labels(step).observe(dt)
        return dt * 1000

    # ------------------------------------------------------------------ guard
    def n_guard(state: AgentState) -> dict:
        t0 = time.perf_counter()
        report = guardrails.check_request(state["request"])
        trace_store.record_event(
            state["run_id"], "guardrail", "input_scan",
            {"blocked": report.blocked, "flags": report.flags}, latency_ms=timed("guard", t0),
        )
        if report.blocked:
            return {"guardrail_flags": report.flags, "phase": "finalize",
                    "escalations": state["escalations"] + [f"input guardrail blocked the request ({report.summary})"]}
        return {"request": report.sanitized_request, "guardrail_flags": report.flags, "phase": "classify"}

    # --------------------------------------------------------------- classify
    def n_classify(state: AgentState) -> dict:
        t0 = time.perf_counter()
        request, run_id = state["request"], state["run_id"]
        claims = detect_claims(request)
        in_scope = True
        if s.corpus_category:
            p = deps.get_prompt("classify")
            msgs = [
                {"role": "system", "content": p.render_system(category=s.corpus_category)},
                {"role": "user", "content": p.render_user(category=s.corpus_category, label_data=_label_data(request))},
            ]
            resp = deps.llm.chat(msgs, step="classify", run_id=run_id, prompt_name=p.name, prompt_version=p.version)
            data = extract_json(resp.text) or {}
            in_scope = bool(data.get("in_scope", False))  # unparseable -> not in scope -> escalate (safe)
        trace_store.record_event(run_id, "run", "classify", {"claims": claims, "in_scope": in_scope}, latency_ms=timed("classify", t0))
        if not in_scope:
            return {"claims": claims, "phase": "finalize",
                    "escalations": state["escalations"] + ["product category is outside the corpus scope"]}
        a = deps.get_prompt("analyst")
        user = a.render_user(
            category=s.corpus_category or "unspecified",
            claims=" | ".join(claims) if claims else "none",
            label_data=_label_data(request),
        )
        messages = [
            {"role": "system", "content": a.render_system(tool_descriptions=tools.TOOL_DESCRIPTIONS, max_tool_calls=s.max_tool_calls)},
            {"role": "user", "content": user},
        ]
        return {"claims": claims, "messages": messages, "phase": "analyst"}

    # ---------------------------------------------------------------- analyst
    def n_analyst(state: AgentState) -> dict:
        run_id = state["run_id"]
        reason = None
        if state["steps"] >= s.max_steps:
            reason = "max_steps"
        elif state["tool_calls"] >= s.max_tool_calls:
            reason = "max_tool_calls"
        elif time.time() > state["deadline"]:
            reason = "wall_clock"
        if reason:
            trace_store.record_event(run_id, "run", "limit_hit", {"reason": reason}, ok=False)
            return {"limit_hit": reason, "draft": None, "phase": "finalize",
                    "escalations": state["escalations"] + [f"agent loop limit reached ({reason})"]}

        t0 = time.perf_counter()
        p = deps.get_prompt("analyst")
        resp = deps.llm.chat(state["messages"], step="analyst", run_id=run_id, prompt_name=p.name, prompt_version=p.version)
        steps = state["steps"] + 1
        msgs = state["messages"] + [{"role": "assistant", "content": resp.text}]
        action = extract_json(resp.text)

        if not action or "action" not in action:
            msgs.append({"role": "user", "content": "OBSERVATION: invalid reply. Respond with exactly one JSON object that has an 'action' field."})
            timed("analyst", t0)
            return {"messages": msgs, "steps": steps, "phase": "analyst"}

        name = action["action"]
        if name == "finish":
            timed("analyst", t0)
            trace_store.record_event(run_id, "run", "analyst_finish", {"n_findings": len(action.get("findings") or [])}, latency_ms=resp.latency_ms)
            return {"messages": msgs, "steps": steps, "draft": action, "phase": "verify"}

        ctx = tools.ToolContext(run_id=run_id, request=state["request"], retriever=deps.retriever,
                                settings=s, retrieved=dict(state["retrieved"]))
        obs, _ok = tools.execute(name, action.get("args") or {}, ctx)
        msgs.append({"role": "user", "content": "OBSERVATION: " + obs})
        timed("analyst", t0)
        return {"messages": msgs, "steps": steps, "tool_calls": state["tool_calls"] + 1,
                "retrieved": ctx.retrieved, "phase": "analyst"}

    # ----------------------------------------------------------------- verify
    def n_verify(state: AgentState) -> dict:
        t0 = time.perf_counter()
        run_id, draft = state["run_id"], state["draft"] or {}
        try:
            findings = [Finding(**f) for f in (draft.get("findings") or [])]
        except (ValidationError, TypeError) as exc:
            findings = None
            malformed = str(exc)[:300]

        if findings is None:
            issues = [{"code": "MALFORMED_FINDING", "finding_index": None, "detail": malformed}]
            ok, strategy = False, None
            resp_dict = {"ok": False, "issues": issues, "suggested_strategy": None, "checks_run": ["schema"], "llm_judge": "skipped"}
        else:
            req = VerifyRequest(
                run_id=run_id, claims_detected=state["claims"], findings=findings,
                retrieved=[PassageRef(**{k: p.get(k, "") for k in ("passage_id", "doc_id", "doc_role", "clause_ref", "text")})
                           for p in state["retrieved"].values()],
            )
            try:
                with span("verify"):
                    resp = deps.verifier.verify(req)
            except VerifierUnavailable as exc:
                trace_store.record_event(run_id, "verification", "verifier_unavailable", {"error": str(exc)}, ok=False)
                metrics.VERIFICATIONS.labels("unavailable").inc()
                return {"phase": "finalize", "degraded": True, "draft": None,
                        "escalations": state["escalations"] + ["verifier agent unavailable; unverified answer withheld"]}
            resp_dict = resp.model_dump(mode="json")
            ok, issues, strategy = resp.ok, resp_dict["issues"], resp.suggested_strategy

        latency = timed("verify", t0)
        trace_store.record_event(run_id, "verification", "verify", {**resp_dict, "attempt": state["attempt"]}, latency_ms=latency, ok=ok)
        metrics.VERIFICATIONS.labels("passed" if ok else "failed").inc()

        if ok:
            esc = list(state["escalations"])
            esc += [f"provisions conflict: {c}" for c in (draft.get("conflicts") or [])]
            esc += [f"an exemption may apply: {e}" for e in (draft.get("possible_exemptions") or [])]
            return {"verification": resp_dict, "findings": [f.model_dump(mode="json") for f in findings],
                    "escalations": esc, "phase": "finalize"}

        attempt = state["attempt"] + 1
        codes = sorted({i["code"] for i in issues})
        if attempt > s.max_verify_attempts:
            return {"verification": resp_dict, "attempt": attempt, "phase": "finalize",
                    "escalations": state["escalations"] + [f"verification failed after {attempt} attempt(s): {', '.join(codes)}"]}

        # --- retry with a different strategy -----------------------------------
        hints = " ".join(STRATEGY_HINTS.get(c, "") for c in codes).strip()
        msgs = state["messages"] + [{"role": "user", "content": "VERIFIER FEEDBACK: " + json.dumps(issues) + " " + hints + " Then finish again."}]
        retrieved, tool_calls = dict(state["retrieved"]), state["tool_calls"]
        missing = [i["detail"] for i in issues if i["code"] == "CLAIM_CONDITION_MISSING"]
        for claim in missing:  # verifier-directed search: scoped to the claims regulation
            if tool_calls >= s.max_tool_calls:
                break
            ctx = tools.ToolContext(run_id=run_id, request=state["request"], retriever=deps.retriever, settings=s, retrieved=retrieved)
            obs, _ = tools.execute("search_corpus", {"query": f"{claim} conditions requirements", "doc": "claims", "k": 4}, ctx)
            msgs.append({"role": "user", "content": "OBSERVATION (verifier-directed search for claim conditions): " + obs})
            retrieved, tool_calls = ctx.retrieved, tool_calls + 1
        return {"messages": msgs, "retrieved": retrieved, "tool_calls": tool_calls, "attempt": attempt,
                "verification": resp_dict, "draft": None, "phase": "analyst"}

    # --------------------------------------------------------------- finalize
    def n_finalize(state: AgentState) -> dict:
        run_id = state["run_id"]
        verified = bool(state.get("verification") and state["verification"].get("ok"))
        findings = [Finding(**f) for f in state.get("findings", [])] if verified else []
        withheld = 0 if verified else len((state.get("draft") or {}).get("findings") or [])
        for f in findings:  # resolve citations to doc/clause for display
            for c in f.citations:
                p = state["retrieved"].get(c.passage_id)
                if p:
                    c.doc_id, c.clause_ref = p.get("doc_id"), p.get("clause_ref")
        escalations = state["escalations"]
        route, reason = router.decide_route(findings, escalations)
        for e in escalations:
            metrics.ESCALATIONS.labels(e.split(":")[0][:40]).inc()
        path = ["analyst agent"]
        if state.get("verification") is not None:
            path.append("verifier agent")
        if escalations:
            path += ["escalated", "regulatory-review"]
        else:
            path.append(route.value)
        result = RunResult(
            run_id=run_id, route=route, reason=reason, findings=findings, escalated=bool(escalations),
            escalation_path=path, degraded=state.get("degraded", False), withheld_findings=withheld,
            claims_detected=state.get("claims", []), prompt_versions=state.get("prompt_versions", {}),
            verification=state.get("verification") or {}, usage=costs.run_usage(run_id),
        ).model_dump(mode="json")
        result, flags = guardrails.filter_output(result)
        if flags:
            trace_store.record_event(run_id, "guardrail", "output_filter", {"flags": flags})
        trace_store.record_event(run_id, "route", "final", {"route": result["route"], "reason": result["reason"]})
        return {"result": result}

    # ------------------------------------------------------------------ graph
    g = StateGraph(AgentState)
    for name, fn in [("guard", n_guard), ("classify", n_classify), ("analyst", n_analyst),
                     ("verify", n_verify), ("finalize", n_finalize)]:
        g.add_node(name, fn)
    g.set_entry_point("guard")
    g.add_conditional_edges("guard", lambda st: st["phase"], {"classify": "classify", "finalize": "finalize"})
    g.add_conditional_edges("classify", lambda st: st["phase"], {"analyst": "analyst", "finalize": "finalize"})
    g.add_conditional_edges("analyst", lambda st: st["phase"], {"analyst": "analyst", "verify": "verify", "finalize": "finalize"})
    g.add_conditional_edges("verify", lambda st: st["phase"], {"analyst": "analyst", "finalize": "finalize"})
    g.add_edge("finalize", END)
    return g.compile()
