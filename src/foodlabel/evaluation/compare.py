"""Compare two evaluation reports: prompt variants (L2) or before/after one change (L3).

    python -m foodlabel.evaluation.compare reports/eval_v1.json reports/eval_v2.json
"""
from __future__ import annotations

import json
import sys

HIGHER_IS_BETTER = {"groundedness": True, "context_relevance": True, "context_precision": True, "faithfulness": True,
                    "hallucination_rate": False, "route_correct": True, "escalation_correct": True,
                    "required_sources_cited": True, "never_clear_for_print": True}


def compare(a: dict, b: dict) -> list[dict]:
    rows = []
    for k, better_high in HIGHER_IS_BETTER.items():
        x, y = a["aggregate"].get(k), b["aggregate"].get(k)
        if x is None or y is None:
            rows.append({"metric": k, "a": x, "b": y, "delta": None, "better": None})
            continue
        d = round(y - x, 4)
        better = "b" if (d > 0) == better_high and d != 0 else ("a" if d != 0 else "tie")
        rows.append({"metric": k, "a": x, "b": y, "delta": d, "better": better})
    return rows


def main() -> None:
    pa, pb = sys.argv[1], sys.argv[2]
    a, b = json.load(open(pa)), json.load(open(pb))
    print(f"A = {a['meta']['tag']} {a['meta']['prompt_versions']}")
    print(f"B = {b['meta']['tag']} {b['meta']['prompt_versions']}\n")
    print(f"{'metric':<26}{'A':>10}{'B':>10}{'delta':>10}  better")
    for r in compare(a, b):
        f = lambda v: "-" if v is None else f"{v:.4f}"
        print(f"{r['metric']:<26}{f(r['a']):>10}{f(r['b']):>10}{f(r['delta']):>10}  {r['better'] or '-'}")


if __name__ == "__main__":
    main()
