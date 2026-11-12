# Demonstration checklist

Rehearse all ten. If the system does not start and serve a valid request, the project scores zero - item 1 comes first.
Suggested driver in brackets (see `team/OWNERSHIP.md`).

| # | Item | How | Driver |
|---|---|---|---|
| 1 | `docker compose up` from a clean clone | fresh `git clone`, add corpus PDFs, `docker compose up --build` | P2 |
| 2 | Valid request -> routed recommendation with resolving citations | web client sample, or `examples/requests/source_of_protein.json` | P1 |
| 3 | One run traced end to end | `GET /v1/runs/{id}/trace` + the same run in Jaeger | P4 |
| 4 | Malformed request quarantined, no agent run | `examples/requests/malformed.json`; show `quarantined` rising and `runs_submitted_total`/queue unchanged | P2 |
| 5 | Case the corpus cannot answer -> escalation | `examples/requests/out_of_scope.json` | P1 |
| 6 | Self-verification catching an unsupported answer | set `STUB_FAULT=wrong_source_once` in `.env`, `docker compose up -d llm-stub worker`, submit the protein request; the trace shows verification failing, a verifier-directed claims search, then passing. `STUB_FAULT=always_bad_quote` shows the escalation instead | P1 |
| 7 | Prompt injection and guardrail response | `examples/requests/prompt_injection.json`; show the `guardrail` trace event and no model call | P3 |
| 8 | Circuit breaker opens, degradation path | `docker compose stop llm-stub` (or `STUB_FAULT=http_500`), submit runs, watch `circuit_breaker_state` go to 2 and runs return `degraded` with route `regulatory-review` | P4 |
| 9 | Locust vs declared target; failing concurrency; effect of `--scale` | `loadtest/README.md` | P4 |
| 10 | The section 4.1 case ("source of protein") | agreed reference answer from Evaluation 1; show both regulations cited | P1 + P3 |
