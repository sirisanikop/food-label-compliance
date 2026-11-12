"""Tools the analyst agent may choose to call (A1). Retrieval is a tool, not a fixed stage.

  search_corpus(query, doc?, k?)  - semantic search, optionally scoped to one regulation
  get_clause(clause_id)           - fetch a passage / clause by identifier
  get_request_field(path)         - read the request's own fields (e.g. nutrition.protein_g)
  compute(op, a, b)               - exact comparison/arithmetic so the model never does maths

Every call has a timeout (TOOL_TIMEOUT_S), is traced, and is metered.
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from foodlabel.common.config import Settings
from foodlabel.observability import metrics, trace_store
from foodlabel.observability.tracing import span

log = logging.getLogger("foodlabel.tools")

TOOL_DESCRIPTIONS = """\
- search_corpus: args {"query": str, "doc": "labelling" | "claims" | null, "k": int 1-8}.
  Semantic search over the regulations. "doc" restricts the search to one regulation.
- get_clause: args {"clause_id": str}. Fetch passages by passage id or clause reference.
- get_request_field: args {"path": str}. Read a field of the label request, e.g. "nutrition.protein_g".
- compute: args {"op": "gte"|"gt"|"lte"|"lt"|"eq"|"ratio_pct"|"add"|"sub"|"mul"|"div", "a": number, "b": number}.
  Exact comparison or arithmetic. ratio_pct returns a / b * 100."""

MAX_PASSAGE_CHARS = 1500
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="tool")


class ToolTimeout(Exception):
    pass


@dataclass
class ToolContext:
    run_id: str
    request: dict
    retriever: Any
    settings: Settings
    retrieved: dict = field(default_factory=dict)  # passage_id -> passage dict (full text)


def run_with_timeout(fn, timeout_s: float):
    fut = _executor.submit(fn)
    try:
        return fut.result(timeout=timeout_s)
    except concurrent.futures.TimeoutError as exc:
        fut.cancel()
        raise ToolTimeout(f"tool exceeded {timeout_s}s") from exc


def _fmt_passage(p: dict) -> str:
    text = p["text"]
    if len(text) > MAX_PASSAGE_CHARS:
        text = text[:MAX_PASSAGE_CHARS] + " ..."
    score = f' score="{p["score"]:.3f}"' if p.get("score") is not None else ""
    return (
        f'<passage id="{p["passage_id"]}" doc="{p.get("doc_role", "")}" '
        f'clause="{p.get("clause_ref", "")}"{score}>\n{text}\n</passage>'
    )


def _search_corpus(args: dict, ctx: ToolContext) -> tuple[str, dict]:
    query = str(args.get("query", "")).strip()
    if not query:
        raise ValueError("query is required")
    doc = args.get("doc")
    if doc not in (None, "labelling", "claims"):
        raise ValueError("doc must be 'labelling', 'claims' or null")
    k = max(1, min(8, int(args.get("k") or ctx.settings.retrieval_k)))
    passages = ctx.retriever.search(query[:300], doc=doc, k=k)
    for p in passages:
        ctx.retrieved[p["passage_id"]] = p
    detail = {"query": query, "doc": doc, "k": k,
              "passages": [{"passage_id": p["passage_id"], "doc_role": p.get("doc_role"), "clause_ref": p.get("clause_ref"), "score": p.get("score")} for p in passages]}
    if not passages:
        return "no passages found", detail
    return "\n".join(_fmt_passage(p) for p in passages), detail


def _get_clause(args: dict, ctx: ToolContext) -> tuple[str, dict]:
    cid = str(args.get("clause_id", "")).strip()
    if not cid:
        raise ValueError("clause_id is required")
    passages = ctx.retriever.get_clause(cid)
    for p in passages:
        ctx.retrieved[p["passage_id"]] = p
    detail = {"clause_id": cid, "passages": [p["passage_id"] for p in passages]}
    if not passages:
        return f"no clause matches '{cid}'", detail
    return "\n".join(_fmt_passage(p) for p in passages), detail


def _get_request_field(args: dict, ctx: ToolContext) -> tuple[str, dict]:
    path = str(args.get("path", "")).strip()
    cur: Any = ctx.request
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise ValueError(f"no such field: {path}")
        cur = cur[part]
    value = json.dumps(cur, default=str)
    return f'<field path="{path}" untrusted="true">{value}</field>', {"path": path}


def _compute(args: dict, ctx: ToolContext) -> tuple[str, dict]:
    op = args.get("op")
    a, b = float(args["a"]), float(args["b"])
    table = {
        "gte": lambda: a >= b, "gt": lambda: a > b, "lte": lambda: a <= b, "lt": lambda: a < b,
        "eq": lambda: abs(a - b) < 1e-9, "add": lambda: a + b, "sub": lambda: a - b,
        "mul": lambda: a * b, "div": lambda: a / b, "ratio_pct": lambda: a / b * 100.0,
    }
    if op not in table:
        raise ValueError(f"unknown op: {op}")
    result = table[op]()
    return json.dumps({"op": op, "a": a, "b": b, "result": result}), {"op": op, "a": a, "b": b, "result": result}


TOOLS = {
    "search_corpus": _search_corpus,
    "get_clause": _get_clause,
    "get_request_field": _get_request_field,
    "compute": _compute,
}


def execute(name: str, args: Any, ctx: ToolContext) -> tuple[str, bool]:
    """Run one tool call. Returns (observation_text, ok). Never raises."""
    t0 = time.perf_counter()
    ok = True
    detail: dict = {"args": args}
    try:
        if name not in TOOLS:
            raise ValueError(f"unknown tool: {name}")
        if not isinstance(args, dict):
            raise ValueError("args must be an object")
        with span(f"tool.{name}"):
            text, extra = run_with_timeout(lambda: TOOLS[name](args, ctx), ctx.settings.tool_timeout_s)
        detail.update(extra)
        metrics.TOOL_CALLS.labels(name, "ok").inc()
    except ToolTimeout as exc:
        ok, text = False, f"<error>{exc}</error>"
        detail["error"] = str(exc)
        metrics.TOOL_CALLS.labels(name, "timeout").inc()
    except Exception as exc:  # noqa: BLE001 - tool errors are observations, not crashes
        ok, text = False, f"<error>{exc}</error>"
        detail["error"] = str(exc)
        metrics.TOOL_CALLS.labels(name, "error").inc()
    latency = (time.perf_counter() - t0) * 1000
    trace_store.record_event(ctx.run_id, "tool", name, detail, latency_ms=latency, ok=ok)
    return text, ok
