# Optional OpenRouter adapter

`app.openrouter.OpenRouterProvider` is an optional gateway behind the same
`ModelProvider.generate(instructions, data, OutputModel, budget=..., cancelled=...)`
boundary as the [direct OpenAI adapter](model-provider.md). Direct OpenAI remains
the default for every agent role and for the main evaluation. This adapter does
not gate core delivery or the required evaluation, and nothing selects it
automatically. Building it did not create an account, buy credits or make a paid
call; all tests use offline fake clients.

## Off by default

`load_openrouter_config()` with no path returns a disabled configuration, and
`config/evaluation.json` has no OpenRouter route. Constructing the provider raises
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
writer = OpenAIWriter.for_job(job, seconds=30, provider=provider)  # or a research worker / planner
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
| `purpose` | `evaluation` (default) pins exactly one route with no fallback. `experiment` requires an `experiment` label. |
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

## Results and recording

Statuses: `completed`, `cancelled`, `timeout`, `budget_denied`, `input_too_large`,
`unsupported_parameters`, `access_denied` (401/403), `insufficient_credits` (402),
`unavailable` (404, for example no endpoint passes the account's provider
allowlist), `invalid_request` (400), `rate_limited` (429), `provider_unavailable`
(502/503, or a 200 body that carries an error), `http_<code>`, `transport_error`,
`malformed_response`, `model_mismatch`, `unknown_usage`, `invalid_output`,
`refused` and `incomplete`. There are no automatic retries.

`result.usage` keeps the keys the existing roles read (`billing`, `actual_usd`,
`reserved_usd`, `tokens`, `requested_model`, `actual_model`, `prompt_version`). It
also records: provider `openrouter`; `purpose` and `experiment`; a
`route_fingerprint` of the pinned settings; endpoint; routing options; requested
and serving provider with `provider_verified`; generation ID; `system_fingerprint`;
the number of endpoints OpenRouter reported; latency; and the full `attempts`
list with each attempt's tokens (including cached, cache-write and reasoning),
cost, reservation and status. The top-level identity fields describe the final
attempt; totals cover all attempts. Each attempt's record is written to the
shared ledger before dispatch and again at settlement, without prompts, answers
or keys.

Missing metadata is reported, never claimed. `metadata_unavailable` lists what
OpenRouter did not return (`serving_provider`, `endpoint_metadata`,
`system_fingerprint`, `actual_model`). `deviations` labels `fallback_used`,
`model_mismatch`, `serving_provider_unconfirmed` (a reported provider name that
does not match the slug, such as "Google" for `google-vertex`) and
`cost_above_configured_rates`.

## Spend, fees and prepaid credits

Before sending, each attempt reserves the whole input allowance at the route's
dearest input/cache rate, plus the output allowance and any per-request charge.
The attempt's actual spend is OpenRouter's reported `usage.cost`, parsed as an
exact Decimal. The usage page calls it credits, the generation-stats page calls
it USD and the FAQ names USD as the base currency, so it is treated as USD.
If the cost or token counts are missing, malformed, floats, or the request failed,
timed out or was cancelled after dispatch, the attempt keeps its full reservation
as uncertain spend. A cost above the reservation, or tokens above either
ceiling, returns `budget_denied` and blocks the campaign for reconciliation (the
existing ledger rule). BYOK responses are refused as unknown: their upstream bill
is outside OpenRouter's `cost`.

Prepaid credits: when the team buys credits, record the purchase once with
`Ledger.credit_purchase(receipt_id, credit_usd, fee_usd)`. Only the
non-refundable platform fee (5.5%, $0.80 minimum, by card; 5% by crypto, per the
FAQ on 2026-11-17) counts as spend at purchase. Each call's consumption is then
counted through its reservation and reported cost, so the credit principal is not
counted twice. Recheck fees before buying; no code here buys credits.

## Main evaluation and experiments

The main evaluation stays on direct OpenAI. If a later decision runs part of it
through OpenRouter, use `purpose: "evaluation"`: one pinned model and provider, no
fallback, and the `route_fingerprint` recorded on every attempt. Optional route or
model comparisons use `purpose: "experiment"` with their own label and are
reported apart from the main results. Pinning removes deliberate substitution,
but OpenRouter and its providers can still change model weights, serving stacks or
metadata, so results are not claimed to be fully reproducible: deviations and
missing metadata are reported instead.

## Account-specific behaviour and limits

- Model and provider availability, regional hosts, privacy settings, guardrails
  and key spending limits are account settings. They can make a route
  `unavailable`, `provider_unavailable` or `access_denied`; the adapter does not
  work around them.
- `data_collection: "deny"` and `zdr: true` exclude providers that do not meet the
  policy. If none remain, OpenRouter refuses the request.
- A dispatched request that times out or is cancelled may still finish and be
  billed. The adapter stops waiting and keeps the reservation.
- Not verified live: account access, the exact `openrouter_metadata` shape, the
  serving-provider name forms and the `max_tokens` capability name. Before any paid
  comparison, run one bounded, ledger-recorded call per route and check these.
- Not implemented: streaming, tool calling, a CLI switch, BYOK accounting and the
  generation-stats lookup (`GET /api/v1/generation`) for later reconciliation.

Sources, checked 2026-11-17:
[chat completions](https://openrouter.ai/docs/api-reference/chat-completion),
[provider routing](https://openrouter.ai/docs/features/provider-routing),
[model fallbacks](https://openrouter.ai/docs/guides/routing/model-fallbacks),
[structured outputs](https://openrouter.ai/docs/features/structured-outputs),
[usage accounting](https://openrouter.ai/docs/use-cases/usage-accounting),
[errors](https://openrouter.ai/docs/api-reference/errors),
[generation stats](https://openrouter.ai/docs/api-reference/get-a-generation),
[privacy](https://openrouter.ai/docs/features/privacy-and-logging),
[BYOK](https://openrouter.ai/docs/use-cases/byok) and
[FAQ](https://openrouter.ai/docs/faq).
