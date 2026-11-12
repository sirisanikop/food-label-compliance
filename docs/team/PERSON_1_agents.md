# Person 1 - Agents

**Requirements:** A1, A2, A3, A5, F3, F4, F5, F6, F7. Read `docs/agent_design.md` first.

## Week-1 priority

Get a valid request through the whole path with the stub model: the code already does this in tests
(`python -m pytest tests/agents`). Then run it for real once Person 2 has the compose file up and Person 3 has the index built.

## Then

1. Replace the stub with a real free-tier model (set `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` in `.env`) and watch what the
   analyst actually does: does it call the claims search on its own (v2) or only after verifier feedback (v1)? That difference
   is your evaluation story.
2. Make the section 4.1 case pass on the real corpus. Agree the reference answer with the team at Evaluation 1.
3. Implement the stuck-run recovery sweep (runbook failure mode 2).
4. Harden the verifier with category-specific checks; keep the deterministic checks as the hard gate.
5. Be ready to explain in the viva: why the route is computed by code, why the verifier is a separate service, what happens when each
   component fails, and what the loop bounds are for.

## Files

`src/foodlabel/agents/`, `src/foodlabel/verifier/`, `src/foodlabel/worker/`, `services/worker/`, `services/verifier/`,
`docs/agent_design.md`, `tests/agents/`.

## You depend on

Person 2 (`common/`), Person 3 (`retrieval`, `prompts`, `guardrails`, `costs`), Person 4 (`observability`, `perf`).
