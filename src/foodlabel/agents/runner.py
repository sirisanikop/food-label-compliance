"""Run one request through the agent graph, with degradation on provider failure (A5)."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from foodlabel.agents.degrade import degraded_result
from foodlabel.agents.graph import Deps, _build_state, build_graph
from foodlabel.agents.llm import LLMClient, LLMError, LLMUnavailable
from foodlabel.agents.verifier_client import HttpVerifier
from foodlabel.common.config import get_settings
from foodlabel.llmops import prompts
from foodlabel.observability import metrics, trace_store

log = logging.getLogger("foodlabel.runner")

PROMPTS_USED = ("classify", "analyst", "verifier_judge")


@dataclass
class AgentOutcome:
    status: str  # completed | degraded | failed
    result: dict
    retrieved: dict = field(default_factory=dict)
    steps: int = 0
    tool_calls: int = 0


_default_deps: Optional[Deps] = None
_default_graph = None


def default_deps() -> Deps:
    global _default_deps
    if _default_deps is None:
        from foodlabel.retrieval.search import get_retriever

        _default_deps = Deps(llm=LLMClient(), retriever=get_retriever(), verifier=HttpVerifier(), settings=get_settings())
    return _default_deps


def run_agent(run_id: str, request: dict, deps: Optional[Deps] = None) -> AgentOutcome:
    deps = deps or default_deps()
    global _default_graph
    if deps is _default_deps:
        if _default_graph is None:
            _default_graph = build_graph(deps)
        graph = _default_graph
    else:
        graph = build_graph(deps)

    versions = prompts.active_versions(PROMPTS_USED)
    state = _build_state(run_id, request, deps.settings, versions)
    t0 = time.perf_counter()
    try:
        final = graph.invoke(state, {"recursion_limit": deps.settings.max_steps * 3 + 20})
        outcome = AgentOutcome("completed", final["result"], final.get("retrieved", {}), final.get("steps", 0), final.get("tool_calls", 0))
        if outcome.result.get("degraded"):
            outcome.status = "degraded"
    except LLMUnavailable as exc:
        log.error("model unavailable; degrading", extra={"run_id": run_id, "error": str(exc)})
        trace_store.record_event(run_id, "error", "llm_unavailable", {"error": str(exc)}, ok=False)
        outcome = AgentOutcome("degraded", degraded_result(run_id, request, "model provider unavailable", versions))
    except LLMError as exc:
        log.error("model rejected the request", extra={"run_id": run_id, "error": str(exc)})
        trace_store.record_event(run_id, "error", "llm_error", {"error": str(exc)}, ok=False)
        outcome = AgentOutcome("failed", degraded_result(run_id, request, "model call failed", versions))
    except Exception as exc:  # noqa: BLE001
        log.exception("agent run crashed", extra={"run_id": run_id})
        trace_store.record_event(run_id, "error", "crash", {"error": repr(exc)}, ok=False)
        outcome = AgentOutcome("failed", degraded_result(run_id, request, "internal error", versions))

    metrics.RUN_DURATION.observe(time.perf_counter() - t0)
    metrics.RUN_STEPS.observe(outcome.steps)
    metrics.RUN_TOOL_CALLS.observe(outcome.tool_calls)
    return outcome
