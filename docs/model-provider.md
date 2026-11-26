# Legacy direct OpenAI provider

The v1 CLI selects the direct Responses API adapter with `python -m app.runs search REQUEST.json --planner model`. It requires a backend `OPENAI_API_KEY` and shared accounting. The default CLI planner uses rules. The browser worker uses [OpenRouter](openrouter.md). [Experiment route and results](buyer-architecture-comparison.md)

The adapter validates structured replies against the role’s output model. Each call reserves its maximum configured token cost before dispatch. A timeout can leave a billable remote request, so unknown usage retains its reservation. The ledger records model, response ID, prompt version, rates, usage and cost while excluding keys, prompts and raw replies. Offline tests use synthetic responses.

[Provider implementation](../app/model_provider.py) · [Shared accounting](shared-accounting.md) · [Original configuration and pricing references](https://github.com/han33-dotcom/TechScout/blob/9006e2ac8557003c958c31f729371ccb8e160b9a/docs/model-provider.md)
