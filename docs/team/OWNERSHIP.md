# Ownership

Four people, four non-overlapping sets of files. Each person pushes only their own files to `main`, so there are
no merge conflicts. If you need a change in someone else's file, ask them (or open an issue), do not edit it.

| | Person 1 - Agents | Person 2 - Platform and API | Person 3 - Retrieval and LLMOps | Person 4 - Reliability, observability, performance |
|---|---|---|---|---|
| Requirements | A1, A2, A3, A5, F3-F7 | A4, A8, D1, D3, D4, F1, F2, F8 | L1-L6, D2 | A6, A7, D5-D8 |
| Code | `src/foodlabel/agents/`, `verifier/`, `worker/` | `src/foodlabel/api/`, `common/` | `src/foodlabel/retrieval/`, `ingest/`, `llmops/`, `evaluation/` | `src/foodlabel/observability/`, `perf/` |
| Services | `services/worker`, `services/verifier` | `services/api`, `services/web`, `docker-compose.yml` | `services/indexer`, `prompts/`, `eval/`, `data/corpus/` | `services/stub_llm`, `deploy/`, `loadtest/` |
| Docs | `docs/agent_design.md` | `README.md`, `docs/architecture.md`, `data_contract.md`, `deployment_boundaries.md`, `demo_checklist.md` | `docs/retrieval.md`, `evaluation.md`, `guardrails.md` | `docs/observability.md`, `slo.md`, `scaling.md`, `runbook.md`, `incident_log.md` |
| Tests | `tests/agents/` | `tests/conftest.py`, `tests/api/` | `tests/llmops/` | `tests/observability/` |

## Interfaces between areas (do not change without telling the others)

- Settings: `foodlabel.common.config.get_settings()` (P2).
- Schemas: `foodlabel.common.schemas` (P2). Changing a model changes a published contract: regenerate `contracts/`.
- Run store and queue: `foodlabel.common.runstore` (P2).
- Retriever: `search(query, doc, k) -> list[passage dict]`, `get_clause(id)` (P3). Passage dict keys:
  `passage_id, doc_id, doc_role, clause_ref, section_path, page, mentions, text, score`. `doc_role` is `labelling` or `claims`.
- Prompts: `llmops.prompts.get_prompt(name)` (P3), files in `prompts/` (P3). Prompt text is P3's; the control logic that
  uses it is P1's. The stub model (P4) depends on the `DETECTED_CLAIMS:` line and `OBSERVATION` message format (P1).
- Guardrails: `check_request(request)`, `filter_output(result)` (P3), called from the graph (P1).
- Cost recording: `llmops.costs.record_llm_call` (P3), called from the model client (P1).
- Metrics and trace events: `observability.metrics`, `observability.trace_store.record_event` (P4), used by everyone.
- Cache and rate limit: `perf.cache`, `perf.ratelimit` (P4), used by the API (P2), worker (P1) and retriever (P3).

## Git workflow

```bash
git pull --rebase origin main     # always first
git add <your files>
git commit -m "<what you did>"
git push origin main
```
Commit under your own name and email. Keep commit messages to your own words.
