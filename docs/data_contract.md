# Data contracts (D1)

Published as JSON Schema in `contracts/` (generated from `src/foodlabel/common/schemas.py` by
`python scripts/export_contracts.py`). Version suffix `.v1`; a breaking change creates `.v2`.

| Interface | Direction | Contract |
|---|---|---|
| `POST /v1/runs` | client -> api | `label_request.v1` |
| `202` body | api -> client | `submit_response.v1` |
| `GET /v1/runs/{id}` | api -> client | `run_view.v1` (embeds `run_result.v1`) |
| `POST /v1/runs/{id}/feedback` | client -> api | `feedback_in.v1` |
| `POST /verify` | worker -> verifier | `verify_request.v1` -> `verify_response.v1` |
| Redis queue `queue:runs` | api -> worker | message = run id string; the run record holds the request |

## Validation applied at the boundary (before any agent starts)

| Kind | Examples |
|---|---|
| Schema / type | required fields, no unknown fields (`extra=forbid`), numbers are numbers |
| Range | `net_quantity` 0-100000, nutrients 0-100 g, `energy_kcal` <= 900, panel area <= 10000 cm2 |
| Category | `veg_mark` in {veg, non_veg, none}; units in {g, kg, ml, l}; `basis` in {per_100g, per_100ml} |
| Format | `fssai_license_number` is exactly 14 digits |
| Cross-field | added sugars <= total sugars <= carbohydrate; saturated + trans fat <= total fat; protein + carbohydrate + fat <= 100; solid units need `per_100g`, liquid units need `per_100ml`; `serving_size` <= pack size |

## Quarantine

A rejected request gets `422` with `{errors: [{field, type, message}], quarantine_id, run_started: false}`.
The record (payload, errors, client, correlation id) is stored in Redis (`GET /v1/quarantine`) and appended to
`rejected.jsonl` on the `quarantine-data` volume. No run id is created and nothing is enqueued, which is the
evidence for demonstration item 4: `run_queue_depth` and `runs_submitted_total` do not change.

## Free-text fields

`product_name`, `category_hint`, `ingredients_text`, `allergen_statement`, `front_of_pack_text`, `label_text`,
`claims_declared[]` are untrusted and pass through the guardrails (`docs/guardrails.md`) before any model sees them.
