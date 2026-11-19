# Incident log (D8)

Record **real** failures you hit while building and testing, as they happen. The assessment requires at least one genuine entry;
a made-up entry is worse than an empty log, so this file intentionally starts empty. Copy the template for each incident.

## Template

### YYYY-MM-DD HH:MM - short title
- **Detected by:** (alert, dashboard, a failing demo run, a teammate...)
- **Symptom:** what was observed
- **Impact:** who/what was affected and for how long
- **Root cause:** what actually went wrong (not just the trigger)
- **Confirmation:** the log line, metric or trace that proved it
- **Recovery:** what was done to restore service
- **Prevention:** what changed so it does not recur (code, config, test, alert, runbook)

## Entries

### 2026-11-19 04:31 UTC - Model name rejected (404 model_not_found)
- **Detected by:** a demo run ending in "Send to regulatory review - degraded: model call failed".
- **Symptom:** every check ended in a degraded result with no findings.
- **Impact:** no real-model check could complete until fixed (about 30 minutes, during first real-model set-up only).
- **Root cause:** `LLM_MODEL` named a model the provider account could not use. The agent correctly refused to guess and degraded.
- **Confirmation:** worker log `model rejected the request ... http 404 ... model_not_found`.
- **Recovery:** listed the models the key could access (`GET /openai/v1/models`) and set `LLM_MODEL` to one on the list.
- **Prevention:** list the provider's models before choosing one (noted in the model-provider comment of `.env.example`).

### 2026-11-19 04:39 UTC - Provider rate limit opened the circuit breaker (429)
- **Detected by:** degraded results ("model provider unavailable") and the worker log.
- **Symptom:** `llm retry ... http 429` five times, then `circuit breaker opened`, then `model unavailable; degrading`.
- **Impact:** affected checks were routed to regulatory review without findings. The system never returned an unsupported answer.
- **Root cause:** the provider's free tier limits tokens per minute; one check makes several model calls, and repeated checks exhausted the allowance. The default breaker threshold (5) and short retry delays gave up too early.
- **Confirmation:** log sequence above; the breaker and degraded route are the designed behaviour (docs/runbook.md, failure mode 1).
- **Recovery:** waited for the allowance to reset; lengthened retries with `LLM_MAX_RETRIES`, `LLM_BACKOFF_BASE_S` and `BREAKER_FAIL_THRESHOLD`.
- **Prevention:** settings documented; no code change was needed because the failure handling worked as designed. A paid or higher-limit key removes the cause.

### 2026-11-19 05:01 UTC - Model answered with a native tool call (400 tool_use_failed)
- **Detected by:** degraded result "model call failed" with `gpt-oss-20b`.
- **Symptom:** `http 400: Tool choice is none, but model called a tool` on the analyst step.
- **Impact:** every check with that model failed on the first call.
- **Root cause:** the model sometimes emits its own tool-call format instead of the JSON action this project's protocol asks for; the provider rejects that, and the client treated every 400 as permanent.
- **Confirmation:** worker log lines with `tool_use_failed`; after the fix, five consecutive attempts still failed with the same error, so this model does not follow the protocol reliably.
- **Recovery:** switched to another model.
- **Prevention:** `agents/llm.py` now retries this specific 400, with a unit test (`tests/agents/test_resilience.py`). `gpt-oss-20b` is recorded as unsuitable in docs/evaluation.md.

### 2026-11-19 05:38 UTC - Request larger than the provider's per-minute input limit (413)
- **Detected by:** degraded result "model call failed" with prompt v2 on `qwen/qwen3.8-27b`.
- **Symptom:** `http 413: Request too large ... input tokens per minute (ITPM): Limit 7000`.
- **Impact:** prompt v2 could not complete on that model at any waiting time; prompt v1 completed.
- **Root cause:** the v2 prompt plus retrieved regulation text exceeded the model's free-tier request size.
- **Confirmation:** the 413 message above.
- **Recovery:** added `PASSAGE_MAX_CHARS` and `SEARCH_MAX_K` and reduced them (700 and 3). **Status: mitigation applied; a completed v2 run on this model has not been confirmed** (the following attempts hit 429).
- **Prevention:** the two settings are in code and `.env.example`; request size is now tunable per provider.

### 2026-11-19 05:48 UTC - Provider API key shown in a screenshot, then rejected (401)
- **Detected by:** review of a shared screenshot, then `http 401 Invalid API Key` after the key was revoked.
- **Symptom:** the key line from the local `.env` was visible in a screenshot.
- **Impact:** the key had to be treated as exposed. Evaluation runs failed until the new key was configured.
- **Root cause:** a diagnostic command printed the whole `.env` line, which included the key.
- **Confirmation:** the screenshot; later a tiny test call returned 401.
- **Recovery:** revoked the key in the provider console and created a new one; put it only in the git-ignored `.env`.
- **Prevention:** diagnostic commands now select only non-secret settings; `.env` stays git-ignored and was confirmed absent from the repository.

