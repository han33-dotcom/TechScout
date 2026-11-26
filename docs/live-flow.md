# Buyer worker and historical checkpoint

Use [the normal buyer worker](buyer-api.md) for current searches and [the experiment record](buyer-architecture-comparison.md) for measured results. The procedure below reproduces the original Class Cellulars phone checkpoint with `app.buyer_flow:run_checkpoint`.

## November 23 checkpoint

| Browser submission | Discovery | Final check | Elapsed | Model spend |
| --- | --- | --- | ---: | ---: |
| First | Four offers from three live reads | Three finalists across two live reads | 20.094 s | USD 0.013705 |
| Repeat | Four stored offers and one current policy read | Three finalists across two live reads | 21.235 s | USD 0.015102 |

Both runs showed zero verified recommendations. Class Cellulars supplied listing
prices, while condition and item-specific delivery eligibility remained unresolved.
The browser showed the known subtotals and marked the payable totals incomplete.
OpenRouter reported OpenAI serving `openai/gpt-6.1-sol` for all eight validated
responses. The shared ledger settled USD 0.028807 with zero uncertain or held spend.

[Traces, versions and per-call usage](https://github.com/han33-dotcom/TechScout/issues/75#issuecomment-7113100350) · [Full checkpoint and limits](https://github.com/han33-dotcom/TechScout/blob/9006e2ac8557003c958c31f729371ccb8e160b9a/docs/live-flow.md)

## Live checkpoint procedure

Set the worker’s backend `OPENROUTER_API_KEY`. Copy `config/inference.example.json` to `var/inference.json` and pin the checkpoint model slug, serving provider, supported parameters and dated decimal prices. Prepare the [shared ledger](local-accounting.md). Use the same application database for the worker and API, run `alembic upgrade head`, then `python -m app.directory import-seeds`.

The bounded rehearsal runs one search and one repeat using stored evidence. Finalists still need live checks. Allow at most four planner/critique calls and one writer reservation within the checkpoint’s USD 1 per-run limit.

**Steps.** Each step runs in a PowerShell terminal at the repository root on the
ledger machine. Steps 2 and 3 keep running, so give each its own terminal and run
step 1 again first in each new terminal.

1. Load the accounting settings, then run the dry check. It sends nothing and prints
   no key. Fix every `MISSING` line before going on.

   ```powershell
   . ./scripts/Use-LocalAccounting.ps1
   $env:DATABASE_URL = '<same database for API and worker>'
   $env:TECHSCOUT_COOKIE_SECURE = '0'
   .venv/Scripts/python.exe -m app.live_check --check-ledger
   ```

2. Build the browser page once, then start the API, which serves it.

   ```powershell
   npm --prefix frontend ci
   npm --prefix frontend run build
   .venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
   ```

3. Start the buyer worker. This is the only process that reads the key.

   ```powershell
   .venv/Scripts/python.exe -m app.run_worker --controller app.buyer_flow:run_checkpoint
   ```

4. In the browser at `http://127.0.0.1:8000`, run one search:
   - Phone, "An exact model", for example `iPhone 17`;
   - storage `256`;
   - a city in Lebanon;
   - Delivery.

   Wait for the result.
5. Repeat the same search once with a new request. Its discovery should report
   `stored`, while the finalists are still re-read live.
6. Record the evidence for each run:

   ```powershell
   .venv/Scripts/python.exe -m app.live_check summarize var/traces/<run_id>-r1.json
   ```

   Record the run ID, date, status, model, serving provider, tokens, latency, settled spend and any uncertain billing with the browser result.
7. Stop both processes (Ctrl+C). The ledger keeps the run's record.
