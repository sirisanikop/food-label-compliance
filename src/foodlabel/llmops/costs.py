"""Token and cost accounting, attributed per step and per run (L5).

Recorded at the time of each model call (not reconstructed later) and stored in the run's
trace. Report with:  python -m foodlabel.llmops.costs --last 100
"""
from __future__ import annotations

import argparse
import json
from typing import Optional

from foodlabel.common.config import get_settings
from foodlabel.observability import metrics, trace_store


def compute_cost(prompt_tokens: int, completion_tokens: int) -> float:
    s = get_settings()
    return prompt_tokens / 1000.0 * s.price_in_per_1k + completion_tokens / 1000.0 * s.price_out_per_1k


def record_llm_call(
    run_id: Optional[str], step: str, *, prompt_tokens: int, completion_tokens: int, latency_ms: float,
    prompt_name: Optional[str] = None, prompt_version: Optional[str] = None,
) -> float:
    cost = compute_cost(prompt_tokens, completion_tokens)
    metrics.LLM_PROMPT_TOKENS.labels(step).inc(prompt_tokens)
    metrics.LLM_COMPLETION_TOKENS.labels(step).inc(completion_tokens)
    metrics.LLM_COST.labels(step).inc(cost)
    metrics.LLM_LATENCY.labels(step).observe(latency_ms / 1000.0)
    if run_id:
        trace_store.record_event(
            run_id, "llm", step, {"prompt": prompt_name}, latency_ms=latency_ms, cost_usd=cost,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, prompt_version=prompt_version,
        )
    return cost


def record_failed_call(
    run_id: Optional[str], step: str, latency_ms: float, outcome: str,
    prompt_name: Optional[str], prompt_version: Optional[str],
) -> None:
    if run_id:
        trace_store.record_event(
            run_id, "llm", step, {"prompt": prompt_name, "outcome": outcome}, latency_ms=latency_ms,
            prompt_version=prompt_version, ok=False,
        )


def run_usage(run_id: str) -> dict:
    s = trace_store.summarize(run_id)
    return {
        "llm_calls": s["llm_calls"], "tool_calls": s["tool_calls"],
        "prompt_tokens": s["prompt_tokens"], "completion_tokens": s["completion_tokens"],
        "cost_usd": s["cost_usd"], "per_step": s["per_step"],
    }


def cost_report(n_runs: int = 100) -> dict:
    from foodlabel.common import runstore

    run_ids = runstore.list_recent_runs(n_runs)
    totals: dict[str, dict] = {}
    runs = 0
    for rid in run_ids:
        u = run_usage(rid)
        if not u["llm_calls"]:
            continue  # cache hit or degraded run with no model call
        runs += 1
        for step, v in u["per_step"].items():
            t = totals.setdefault(step, {"cost_usd": 0.0, "tokens": 0, "calls": 0})
            t["cost_usd"] += v["cost_usd"]
            t["tokens"] += v["prompt_tokens"] + v["completion_tokens"]
            t["calls"] += v["calls"]
    total_cost = sum(t["cost_usd"] for t in totals.values())
    key = "cost_usd" if total_cost > 0 else "tokens"  # free tier: rank by tokens
    driver = max(totals, key=lambda k: totals[k][key]) if totals else None
    return {
        "runs_with_model_calls": runs,
        "cost_per_100_runs_usd": round(total_cost / runs * 100, 6) if runs else 0.0,
        "largest_cost_driver_step": driver,
        "ranked_by": key,
        "per_step": totals,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Cost per 100 runs and largest cost driver")
    ap.add_argument("--last", type=int, default=100)
    print(json.dumps(cost_report(ap.parse_args().last), indent=2))
