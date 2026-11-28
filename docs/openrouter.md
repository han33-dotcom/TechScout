# OpenRouter adapter

The baseline buyer worker builds its model roles from the pinned runtime route in [app/inference.py](../app/inference.py). The [experiment record](https://github.com/han33-dotcom/TechScout/blob/baseline/pre-scout/docs/buyer-architecture-comparison.md) names the model and reasoning effort used for comparison. The legacy direct provider is documented [separately](https://github.com/han33-dotcom/TechScout/blob/baseline/pre-scout/docs/model-provider.md).

## Off by default

`load_openrouter_config()` without a path returns a disabled standalone configuration. The [evaluation configuration](../config/evaluation.json) records the study route. Constructing the provider raises
`ProviderUnavailable` (no request, no reservation) when:

- the configuration does not set `"enabled": true`; or
- the backend has no `OPENROUTER_API_KEY` (an OpenAI key is not accepted in its place).

Keep the key in the backend environment only: never in a configuration file, the
React bundle, a commit or a log. The key lives only inside the transport closure;
results, ledger records, `repr()` and logs never contain it, and provider error
text is never copied. It is sent only to the code-owned endpoint allowlist:
`https://openrouter.ai/api/v1/chat/completions` (default), or the documented
`eu.`/`us.` regional hosts for accounts that have them.

To use it, put a configuration file outside git (for example `var/openrouter.json`;
`var/` is ignored), then pass the provider to a role explicitly:

```python
from app.openrouter import OpenRouterProvider, load_openrouter_config
provider = OpenRouterProvider(load_openrouter_config('var/openrouter.json'))
planner = OpenAIPlanner(provider)  # research workers take it as their provider the same way
```

Each role's prompt version and input ceiling come from the provider's settings,
so a role with its own prompts needs its own configuration file. The writer
needs `"prompt_version": "writer-v1"` and an input ceiling whose reservation
fits the USD 0.25 final-phase reserve (the direct writer uses 32,768 tokens).
With the general `roles-v1` version the writer finds no prompt and returns its
deterministic template without calling the model (`reason: unknown_prompt`).

```python
writer_provider = OpenRouterProvider(load_openrouter_config('var/openrouter-writer.json'))
writer = OpenAIWriter.for_job(job, seconds=30, provider=writer_provider)
```

## Configuration

```json
{"openrouter": {
  "enabled": true,
  "purpose": "evaluation",
  "routes": [{
    "model": "<author/slug from OpenRouter>",
    "provider": "<approved serving-provider slug>",
    "supported_parameters": ["max_tokens", "response_format", "structured_outputs"],
    "rates": {"input": "<USD per 1M prompt tokens>", "cached": "<...>",
              "cache_write": "<...>", "output": "<USD per 1M completion tokens>", "request": "0"}
  }],
  "data_collection": "deny",
  "zdr": false,
  "max_input_tokens": 65536,
  "max_output_tokens": 4096,
  "timeout_seconds": 30,
  "prompt_version": "roles-v1",
  "pricing_checked_on": "<date the rates were copied>"
}}
```

The values in angle brackets are placeholders. Copy the model ID, the serving
provider's slug, its `supported_parameters` and its current prices from that
provider's endpoint on OpenRouter during setup. Unknown keys are rejected.

| Field | Rule |
| --- | --- |
| `routes[].model` | One explicit `author/slug`. Router models (`openrouter/...`) and `:floor`, `:nitro`, `:online`, `:free` style variants are rejected: they pick providers, add hosted tools or change pricing. |
| `routes[].provider` | The single approved serving provider for that route. |
| `routes[].supported_parameters` | Must include `response_format`, `structured_outputs` and `max_tokens` (and `reasoning` when `effort` is set). Otherwise the call returns `unsupported_parameters` without sending or reserving. |
| `routes[].rates` | Decimal strings. An enabled route needs positive input and output rates: an unknown cost is never zero. |
| `purpose` | `evaluation` (default) pins exactly one route with no fallback. `buyer` applies the same pinning to live buyer runs (built by `app.inference`). `experiment` requires an `experiment` label starting with `exp-`. |
| `allow_fallback`, `fallback_on` | Off by default. Only an experiment may list up to three routes and turn this on (see below). |
| `data_collection`, `zdr` | Data-policy filters sent to OpenRouter. `deny` is the default. |
| `effort` | Optional `reasoning.effort`; omitted by default. |

## What each request sends

Every attempt sends one `model` and
`provider: {order: [p], only: [p], allow_fallbacks: false, require_parameters: true, data_collection}`
(plus `zdr: true` when configured). OpenRouter's `allow_fallbacks` defaults to
true, so it is always sent as false. The request also has a strict
`response_format` JSON schema, `max_tokens`, `stream: false` and the
`X-OpenRouter-Metadata: enabled` header. It never sends `models`, `route`, `sort`,
`tools`, `plugins` or `user`. The answer is validated locally with the role's
Pydantic model; a tool call, refusal, cut-off answer or schema violation is not
returned.

The returned `model` must equal the requested ID exactly (also inside the
message, when present). Anything else is `model_mismatch`: the answer is
discarded and the attempt's cost is still recorded.

## Fallback: off unless explicitly configured

By default a failed attempt is returned as is; there is no second request. In an
`experiment`, `allow_fallback: true` with two or three routes permits a fallback
only after `provider_unavailable`, `rate_limited` or `unavailable` (whichever are
listed in `fallback_on`). Each fallback is a new request with its own spend
reservation and ledger record. It shares the original deadline (`timeout_seconds`
covers all attempts), stops at cancellation, and is refused when the shared budget
cannot reserve it. Invalid output, model substitution, bad requests and timeouts
never fall back.

`usage['fallback']` records `allowed`, `used` (a fallback request was actually
sent), the `triggers` and `refused`: the status of a fallback that was stopped
before sending (budget, deadline or cancellation). A refused fallback leaves
the result of the last request actually sent, for example `provider_unavailable`,
and the top-level identity fields describe that request. The failed attempt's
cost is unknown and keeps its reservation. The v1 run controller budget
(`AccountedRunBudget`) refuses all further spend in a run after an unknown cost,
so a fallback can only be sent under a ledger job budget (`JobBudget`).

## Results and recording

Statuses: `completed`, `cancelled`, `timeout`, `budget_denied`, `input_too_large`,
`unsupported_parameters`, `access_denied` (401/403), `insufficient_credits` (402),
`unavailable` (404, for example no endpoint passes the account's provider
allowlist), `invalid_request` (400), `rate_limited` (429), `provider_unavailable`
(502/503), `http_<code>`, `transport_error`, `malformed_response`,
`model_mismatch`, `unknown_usage`, `invalid_output`, `refused` and `incomplete`.
A 200 reply whose body carries an error is mapped by its `error.code` in the same
way, or `malformed_response` without an integer code; its cost stays unknown.
There are no automatic retries.

`result.usage` keeps the keys the existing roles read (`billing`, `actual_usd`,
`reserved_usd`, `tokens`, `requested_model`, `actual_model`, `prompt_version`). It
also records: provider `openrouter`; `purpose` and `experiment`; a
`route_fingerprint` of the pinned settings; endpoint; routing options; requested
and serving provider with `provider_verified`; generation ID; `system_fingerprint`;
the number of endpoints OpenRouter reported; latency; and the full `attempts`
list with each attempt's tokens (including cached, cache-write and reasoning),
cost, reservation and status. The top-level identity fields describe the last
request actually sent; money and token totals cover every attempt that still holds
spend. Each attempt's record is written to the
shared ledger before dispatch and again at settlement, without prompts, answers
or keys.

Missing metadata is reported, never claimed. `metadata_unavailable` lists what
OpenRouter did not return (`serving_provider`, `endpoint_metadata`,
`system_fingerprint`, `actual_model`). `deviations` labels `fallback_used`,
`model_mismatch`, `serving_provider_unconfirmed` (a reported provider name that
does not match the slug, such as "Google" for `google-vertex`),
`cost_above_configured_rates` and `cost_below_configured_rates` (under half the
configured-rate estimate, which can mean a price change or an undeclared BYOK fee).

## Spend, fees and prepaid credits

Before sending, each attempt reserves the whole input allowance at the route's
dearest input/cache rate, plus the output allowance and any per-request charge.
The attempt's actual spend is OpenRouter's reported `usage.cost`, parsed as an
exact Decimal. The usage page calls it credits, the generation-stats page calls
it USD and the FAQ names USD as the base currency, so it is treated as USD.
If the cost or token counts are missing, malformed, floats, or the request failed,
timed out or was cancelled after dispatch, the attempt keeps its full reservation
as uncertain spend. A reported cost above the reservation returns
`budget_denied`, is recorded, and blocks the campaign for reconciliation (the
existing ledger rule). Token counts above either ceiling also return
`budget_denied` and discard the answer, but their known cost is settled normally
and does not block the campaign. The cost is accepted only when the reply states
`is_byok: false`. A BYOK reply, or one without that flag, is treated as unknown,
because under BYOK `cost` is only OpenRouter's fee and the provider bills the rest
upstream.

Prepaid credits: when the team buys credits, record the purchase once with
`Ledger.credit_purchase(receipt_id, credit_usd, fee_usd)`. Only the
non-refundable platform fee (5.5%, $0.80 minimum, by card; 5% by crypto, per the
FAQ on 2026-11-17) counts as spend at purchase. Each call's consumption is then
counted through its reservation and reported cost, so the credit principal is not
counted twice. Recheck fees before buying; no code here buys credits.

## Evaluation and experiments

Record the model, serving provider, reasoning effort, prompt versions, token usage, settled spend and uncertain reservations for each run. Pin the route used by both architectures and label any model or fallback experiment separately. Use the [applicable study protocol](https://github.com/han33-dotcom/TechScout/blob/baseline/pre-scout/docs/buyer-correctness-acceptance.md) and the [comparison record](https://github.com/han33-dotcom/TechScout/blob/baseline/pre-scout/docs/buyer-architecture-comparison.md) for current work. The [older adapter report](https://github.com/han33-dotcom/TechScout/blob/9006e2ac8557003c958c31f729371ccb8e160b9a/docs/openrouter.md) records account-specific limits and the original provider plan.
