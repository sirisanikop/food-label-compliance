# Agent design

## Topology (A2)

Two agents, two services, one defined hand-off.

```
 api --queue--> worker (ANALYST agent) --HTTP POST /verify--> verifier (VERIFIER agent)
                     |  tools: search_corpus, get_clause, get_request_field, compute
                     +--> model provider (breaker "llm")        +--> optional entailment judge (breaker "verifier-llm")
```

| Agent | Responsibility | Never does |
|---|---|---|
| Analyst (`agents/graph.py`) | decide which provisions to retrieve, compare declared values, draft findings with citations | decide the route; return an answer the verifier has not passed |
| Verifier (`verifier/`) | check every citation resolves to retrieved text, quotes are verbatim, numbers are consistent, every claim cites the claims regulation | retrieve or write findings |

The route (`clear-for-print` / `revise-and-recheck` / `regulatory-review`) is computed from verified findings by
`agents/router.py`, not by a model.

## Graph (A1)

`guard -> classify -> analyst <-> tools -> verify -> finalize`, built with LangGraph. The analyst model chooses an action
each turn: call one of four tools or `finish`. Retrieval is a tool it calls (it may call it zero, one or many times).

Loop bounds: `MAX_STEPS` (model turns), `MAX_TOOL_CALLS`, `RUN_TIMEOUT_S` (wall clock), `MAX_VERIFY_ATTEMPTS`. Hitting a
bound escalates; it never returns a partial answer.

## The difficult case (section 4.1)

The labelling regulation does not state the conditions for a claim. After the analyst finishes, the verifier requires
that each detected claim has a finding citing a `claims`-role passage. If not (`CLAIM_CONDITION_MISSING`) the graph does
**not** just re-ask: it runs a verifier-directed `search_corpus(query="<claim> conditions requirements", doc="claims")`
itself and adds the result to the analyst's context (a different retrieval strategy), then lets the analyst answer again.
After `MAX_VERIFY_ATTEMPTS` it escalates. `prompts/analyst.v2.yaml` additionally instructs the model to look for modifying
provisions itself; the evaluation compares v1 and v2.

## Verification checks (A3) - `verifier/checks.py`

`CITATION_NOT_RETRIEVED`, `PASSAGE_TAMPERED` (passage id embeds a hash of its text), `QUOTE_NOT_IN_PASSAGE`, `NO_CITATION`,
`CLAIM_CONDITION_MISSING`, `NUMERIC_MISMATCH` (status recomputed from `values`), `THRESHOLD_NOT_IN_QUOTE`, and optionally
`NOT_ENTAILED` from an LLM judge (`VERIFIER_USE_LLM=true`). Deterministic checks are the hard gate.

## Escalation (F7)

Escalates to `regulatory-review` with a stated reason when: category is outside the corpus; provisions conflict or an
exemption may apply (reported by the analyst); verification fails after the allowed attempts; a loop bound is hit; the
input guardrail blocks; the verifier is unavailable (answer withheld, run marked degraded).

## Failure handling (A5)

| Failure | Behaviour |
|---|---|
| Model timeout / 429 / 5xx | retry with exponential backoff + jitter, bounded by `LLM_MAX_RETRIES` |
| Repeated model failure | circuit breaker opens (`BREAKER_FAIL_THRESHOLD`), calls fail fast, half-open probe after `BREAKER_RESET_S` |
| Model unavailable during a run | **degraded path** (`agents/degrade.py`): deterministic pre-check, route `regulatory-review`, `degraded: true`, no findings |
| Verifier unreachable | its own breaker; analyst answer withheld; run escalates and is marked degraded |
| Tool timeout / error | returned to the model as an observation (counts against the tool budget) |
| Worker crash mid-run | the run stays `running`; see `docs/runbook.md` for recovery (a run is lost, the queue is not) |

A run that did not complete is never returned as a confident answer.

## What to build next (Person 1)

- A recovery sweep that re-queues runs stuck in `running` past `RUN_TIMEOUT_S` (see runbook failure mode 2).
- Exemption/conflict detection beyond what the model reports (for example compare the labelling and claims passages for the same nutrient).
- Category-specific claim lexicon in `agents/claims.py`.
- Tune `analyst.v2` against the real corpus once indexed, together with Person 3.
