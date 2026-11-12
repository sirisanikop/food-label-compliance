"""Batch evaluation from the command line (L3).

    python -m foodlabel.evaluation.run_eval --set eval/eval_set.jsonl --tag baseline
    python -m foodlabel.evaluation.run_eval --set eval/eval_set.jsonl --prompt analyst=v2 --tag v2

Runs every scenario through the real agent graph (same code as production), computes the four
metrics plus outcome accuracy, and writes reports/eval_<tag>.json. Use evaluation/compare.py to
compare two reports (prompt variants, or before/after one deliberate change).
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import time
import uuid

from foodlabel.evaluation import metrics as m


def load_scenarios(path: str) -> list[dict]:
    return [json.loads(line) for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def score_scenario(sc: dict, result: dict, retrieved: dict) -> dict:
    findings = result.get("findings", [])
    exp_roles = sc.get("must_cite_roles", [])
    cited_roles = {retrieved[c["passage_id"]]["doc_role"] for f in findings for c in f.get("citations", []) if c.get("passage_id") in retrieved}
    out = {
        "id": sc["id"], "group": sc["group"], "route": result.get("route"), "escalated": result.get("escalated"),
        "degraded": result.get("degraded"), "n_findings": len(findings),
        "groundedness": m.groundedness(findings, retrieved),
        "faithfulness": m.faithfulness(findings),
        "hallucination_rate": m.hallucination_rate(findings, retrieved),
        "context_relevance": m.context_relevance(retrieved, sc.get("expected_passages", [])),
        "context_precision": m.context_precision(findings, retrieved, sc.get("expected_passages", [])),
        "required_sources_cited": (set(exp_roles) <= cited_roles) if exp_roles else None,
        "route_correct": (result.get("route") == sc["expected_route"]) if sc.get("expected_route") else None,
        "escalation_correct": (bool(result.get("escalated")) == sc["expect_escalation"]) if sc.get("expect_escalation") is not None else None,
        "never_clear_for_print": (result.get("route") != "clear-for-print") if sc.get("must_not_clear") else None,
        "cost_usd": (result.get("usage") or {}).get("cost_usd"),
    }
    return out


def aggregate(rows: list[dict]) -> dict:
    keys = ["groundedness", "context_relevance", "context_precision", "faithfulness", "hallucination_rate"]
    agg = {k: m.mean(r[k] for r in rows) for k in keys}
    for k in ("route_correct", "escalation_correct", "required_sources_cited", "never_clear_for_print"):
        vals = [r[k] for r in rows if r[k] is not None]
        agg[k] = round(sum(vals) / len(vals), 4) if vals else None
    agg["scenarios"] = len(rows)
    agg["degraded_runs"] = sum(1 for r in rows if r["degraded"])
    agg["total_cost_usd"] = round(sum(r["cost_usd"] or 0 for r in rows), 6)
    return agg


def evaluate(scenarios: list[dict]) -> dict:
    from foodlabel.agents.runner import run_agent

    rows = []
    for sc in scenarios:
        t0 = time.perf_counter()
        outcome = run_agent(uuid.uuid4().hex, sc["request"])
        row = score_scenario(sc, outcome.result, outcome.retrieved)
        row["latency_s"] = round(time.perf_counter() - t0, 2)
        rows.append(row)
        print(f"{sc['id']:<28} route={row['route']:<18} grounded={row['groundedness']:.2f} halluc={row['hallucination_rate']:.2f}")
    return {"aggregate": aggregate(rows), "scenarios": rows}


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the evaluation set")
    ap.add_argument("--set", default="eval/eval_set.jsonl")
    ap.add_argument("--tag", default="run")
    ap.add_argument("--prompt", action="append", default=[], help="name=version, e.g. analyst=v2 (repeatable)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--out-dir", default="reports")
    a = ap.parse_args()
    for spec in a.prompt:
        name, version = spec.split("=")
        os.environ[f"PROMPT_VERSION_{name.upper()}"] = version
    scenarios = load_scenarios(a.set)[: a.limit]
    report = evaluate(scenarios)
    report["meta"] = {"tag": a.tag, "prompt_overrides": a.prompt, "set": a.set, "ran_at": time.time(),
                      "prompt_versions": {n: os.environ.get(f"PROMPT_VERSION_{n.upper()}", "v1") for n in ("classify", "analyst", "verifier_judge")}}
    out = pathlib.Path(a.out_dir)
    out.mkdir(exist_ok=True)
    path = out / f"eval_{a.tag}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\nAGGREGATE", json.dumps(report["aggregate"], indent=2))
    print("report:", path)


if __name__ == "__main__":
    main()
