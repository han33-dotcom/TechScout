# Direct OpenAI model boundary

`app.model_provider.OpenAIProvider` is the default hosted-model adapter. It uses
the Responses API and the `model_provider` section of `config/evaluation.json`.
`python -m app.runs search REQUEST.json --planner model` selects its v1 menu
bridge. The CLI's default remains the offline rule baseline; `none` remains the
no-investigation baseline. The old Anthropic planner/configuration stays available
for explicitly selected historical evaluations, with no automatic substitution.

Backend workers supply `OPENAI_API_KEY`. Startup, offline tests, and the React
bundle require no provider credentials. OpenRouter is not installed or required.
An optional future gateway must implement `ModelProvider.generate`, including its
own actual provider/model attribution and all-inclusive pricing; agent roles can
use that typed boundary without changing orchestration.

Full-product roles call `generate(instructions, data, OutputModel,
budget=JobBudget(job), cancelled=...)`. The output is validated with the supplied
Pydantic model. Roles remain separate from code-owned eligibility, costs, tool
allowlists, and accounting. Their implementation/integration belongs to #67/#68.

Every attempt reserves before sending. The complete configured input ceiling,
including schema/framing, is priced at the most expensive configured input/cache
rate; output includes reasoning tokens. Prompts are bounded well below the long
context surcharge. Standard service tier is explicit; there are no hosted tools,
automatic retries, provider fallbacks, or implicit model changes. Unknown models,
usage, cache lifetime, or service tiers retain the full reservation. An unpriced
configured model is never sent. A returned model version needs its own configured
price; the alias is not substituted for the reported model.

The shared ledger stores requested/actual model, response ID, prompt version,
pricing date/rates, token counts and known/unknown cost before settlement. It
stores no API key, prompt, raw answer or provider error body. A crash leaves the
pre-dispatch unknown-cost record and conservative reservation. Recovery never
replays it. Timeout/cancellation stops waiting within a bounded poll interval;
an already dispatched remote request may finish and be billed. Its late reply
cannot publish an answer or refund its reservation.

The official [model page](https://developers.openai.com/api/docs/models/gpt-6.1-sol)
confirms `gpt-6.1-sol`; rates were checked on 2026-11-17. The
[Responses reference](https://developers.openai.com/api/reference/resources/responses/methods/retrieve)
defines usage fields, and [prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)
describes the separate 30-minute/12-hour write rates. Structured JSON follows the
[structured output guide](https://developers.openai.com/api/docs/guides/structured-outputs).

**Account access and live output quality have not been verified.** Before paid
calibration, configure the account and run one explicitly bounded, ledger-recorded
check with this exact model. Authentication/model-access errors are returned as
`access_denied`; changing provider/model requires an explicit configuration change.
All acceptance tests for this slice use offline clients and synthetic responses.
