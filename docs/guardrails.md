# Guardrails (L4)

Agents are more exposed than pipelines: injected text can reach a model that holds tools. Defence in layers:

| Layer | Where | What |
|---|---|---|
| Boundary validation | API | strict schema, length limits on every free-text field |
| Input scan | graph node `guard`, before any model call | invisible/control characters stripped; instruction-override, role-hijack, route-forcing, prompt-exfiltration and chat-markup patterns flagged HIGH; delimiter tags neutralised; toxicity scored |
| Blocking | graph | any HIGH flag or toxic input -> run escalates to `regulatory-review` with **no model call**, flags recorded in the trace |
| Data/instruction separation | prompts | label data always inside `<label_data>`; passages inside `<passage>`; field reads inside `<field untrusted>`; prompts state these are data, never instructions |
| Capability limits | architecture | the model has no tool that sends, writes or approves anything; tools are read-only and arguments are validated |
| Output filter | `finalize` | route must be one of the three allowed values; "approved for print", "fully compliant", "guarantee" etc. are replaced; disclaimer always present; toxicity check on output |
| Verifier | separate agent | an answer that is not grounded in retrieved text is never returned, whatever the model was talked into saying |

`examples/requests/prompt_injection.json` is the demonstration input. Patterns are in `llmops/guardrails.py` and tested in
`tests/llmops/test_guardrails.py`; add every red-team input you try as a test.

Limits to state honestly in the report: pattern matching misses novel phrasings; the strongest protections are the structural ones
(read-only tools, route computed by code, verifier, output filter). The toxicity check is a small lexicon; replace it with a classifier
if you have one available.
