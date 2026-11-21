# First browser-to-result flow and live-run runbook

Issue [#75](https://github.com/han33-dotcom/TechScout/issues/75) (T39) connects the
existing pieces into one buyer flow. This page describes what is wired and how it was
checked offline. It also gives the exact steps for the one bounded live run that is
still open.

**Status:** the offline wiring is done and tested. No live or paid call has been made.
The live boxes on #75 stay open until the run below is performed and recorded.

## What the flow does

The buyer worker runs the controller `app.buyer_flow:run`:

```text
python -m app.run_worker --controller app.buyer_flow:run
```

**Scope.** One source (Class Cellulars) and one product type: an exact-model phone
search, delivered in Lebanon. Every other request ends as an honest abstention that
says why:

- a needs-based search, or another category: outside this first flow;
- no delivery city: the route and its charges cannot be checked;
- pickup: choosing a branch is not supported yet.

**Steps.** One run goes through these phases, which the browser shows as progress:

1. **Interpret.** Check the scope (above).
2. **Discover.** Read the listings through the source gate and the shared ledger. A
   repeat search can reuse listings stored by an earlier run (stored evidence). The
   FAQ policy page is always read in the current run.
3. **Research.** The orchestrator plans research over the listings. The research
   workers and the evidence critique run inside it.
4. **Evaluate.** The fixed rules decide eligibility and cost.
5. **Verify.** The run enters its final phase and re-reads the three best finalists
   live.
6. **Write.** The writer explains the checked results.
7. **Publish.** The result appears in the browser.

**Who decides what.** Models only choose: which listings to investigate, which
evidence issue deserves one follow-up, and which checked claims to show. Code decides
eligibility, cost and verification. A finalist is recommended only when its price and
stock come from the final re-read (`app.verification`, #70).

**Format conversion.** `app.buyer_sources` converts the Class Cellulars reader's v1
offers into v2 offers. It moves facts and their evidence; it never fills a gap. Three
facts stay unknown, with a reason, because the store does not publish them in a form
the v2 contract accepts:

- the store-wide free-shipping banner is not evidence for this delivery route;
- there is no per-item delivery eligibility;
- the phone's condition is not stated.

So with today's real Class Cellulars pages, the expected live outcome is the **honest
unresolved case**. Each candidate is listed separately, with what is still unknown.
The verified path is demonstrated offline with clearly labelled synthetic route facts.

**What the browser shows.** `RunSnapshot.explanation` is a new optional field in the
v2 contract. It holds the writer's plain-text explanation and its source (`model`,
`mixed` or `fallback`). Older snapshots without it remain readable. The browser renders
it as text in the progress panel.

## Inference: one pinned OpenRouter route

`app.inference` builds an `OpenRouterProvider` for every model-driven role:

- the orchestrator plan;
- the research workers' resource choice;
- the evidence critique;
- needs-based candidate selection (not used by this exact-model flow);
- the writer.

The configuration lives outside git: `var/inference.json`, or the path in
`TECHSCOUT_INFERENCE_CONFIG`. Start from
[`config/inference.example.json`](../config/inference.example.json).

| Setting | Rule |
| --- | --- |
| `route` | One explicit `author/slug` model and one approved serving provider. There is no second route or fallback setting, and the adapter always sends `allow_fallbacks: false`. Router models are rejected. |
| `rates`, `pricing_checked_on` | Decimal USD per million tokens, copied from the provider's endpoint on OpenRouter, plus the date they were copied. A missing or zero price, or a missing date, fails when the file is loaded. |
| `roles` | Orchestrator, research workers and critique use prompt version `roles-v1`. Default allowance: 16,384 input and 2,048 output tokens. One call must fit the USD 0.75 research allowance. |
| `writer` | Prompt version `writer-v1`. Default allowance: 32,768 input and 4,096 output tokens. One call must fit the USD 0.25 final-phase reserve. |
| `purpose` | Always `buyer`: one pinned route, no fallback, no experiment label. |

**Failures are explicit.** Any of the following makes the worker publish an `error`
run that names the problem, before anything is reserved or sent:

- no configuration file;
- an invalid configuration;
- an unknown key (for example an `api_key` field in the file);
- a missing `OPENROUTER_API_KEY`.

**The key stays in the backend.** `OPENROUTER_API_KEY` is read only by the buyer
worker process. A worker started on the host also reads it from the repository-root
`.env`: only that one variable, and never replacing a value already set. The API
process never reads it. The key never appears in:

- `VITE_` variables or the browser bundle;
- snapshots;
- the ledger;
- logs, traces or exports.

**Direct OpenAI is never used.** The worker never builds the direct OpenAI provider
and does not read `OPENAI_API_KEY`. Tests run the whole flow with that key unset and
with the direct provider patched to fail if it is ever constructed.

**Hand-off to #80.** The frozen `config/evaluation.json`, `eval/models.json` and the
protocol hashes are unchanged; they still name the direct OpenAI route. #80 versions
the protocol and pins the same validated OpenRouter route for evaluation. The values
confirmed in `var/inference.json` (model slug, serving provider, prices, date) are
the input to that change.

## Limits

**Deadline.** The run gets the API's active-time allowance (120 seconds) and stops
starting new work after it:

- research ends by 60 seconds;
- non-final ledger reservations end by 90 seconds;
- the final phase ends by 118 seconds, inside the ledger's 120-second limit;
- the writer is called only when at least 5 seconds remain; otherwise the
  deterministic summary is used and the run is `partial`.

A provider timeout keeps that call's full reservation as uncertain spend.

**Cancellation.** A buyer's cancellation, or a lost worker lease, does three things:

- cancels the ledger job, so no new reservation succeeds;
- stops the provider waits, and late replies are discarded;
- stops publication.

**Spend.** Every model call reserves its full worst-case cost in the shared ledger
before it is sent. The ledger caps one buyer run at:

- USD 1 in total;
- USD 0.75 and 30 calls before the final phase;
- 40 calls in all.

A refused reservation sends nothing. The run is then marked `partial` with "spend" as
the reason.

**Unknown cost.** A reply without a reported cost keeps the full reservation as
uncertain spend. The trace says so; it is never counted as zero.

**Wasted calls.** Research workers no longer ask a model to choose a page that no
reviewed extraction profile can read (#75 change in `app.research_workers`). They
record the missing field as an explicit gap instead. No reviewed profile reads Class
Cellulars pages yet, so live research uses only evidence already stored in the
public corpus.

**Known gap.** A clarification resume keeps the same ledger job, so its
wall-clock deadline keeps running while the buyer answers. This flow never asks for a
clarification, so it does not hit this. Needs-based flows (#76) must address it.

## Trace

Each run writes a sanitized trace to `var/traces/<run_id>-r<revision>.json`. It
records:

- **Versions:** the code commit, the inference configuration's SHA-256, the route
  fingerprints, prompt versions, prices and their date, the source registry's
  SHA-256 and the adapter version.
- **Phases and limits:** phase timings, the deadlines, and what stopped the run.
- **Source reads:** every fetch (URL, status, response SHA-256, observation time),
  in both the discovery and the final re-read.
- **Model calls:** for every call, its role and schema, and whether the answer passed
  local Pydantic validation. Its usage: requested and reported model, requested and
  serving provider, endpoint, tokens, latency, reserved and actual USD, and billing
  status. Also any deviations, and the metadata OpenRouter did not report.
- **Spend:** this run's ledger attempts: known USD, uncertain USD (kept full
  reservations) and still-held USD.

It never contains a prompt, an answer, a key or buyer free text.

## Offline evidence

Run `tests/test_buyer_flow.py`. It goes through the real owned-run API, worker,
SQLite ledger, Class Cellulars reader and OpenRouter adapter. OpenRouter is a fake
HTTP transport, and the store is fake source HTTP serving the dated captures with
the real clock. It covers:

- an honest unresolved run;
- a verified run with synthetic route facts, and the writer;
- a stored-evidence repeat that still re-reads finalists live;
- OpenRouter only, with no OpenAI key and no fallback;
- the key never leaking;
- cancellation, deadline, provider timeout and spend-cap stops;
- missing metadata and unreported cost;
- configuration failures;
- the dry-run check.

## Live run (not yet performed)

**What it needs.**

1. **`OPENROUTER_API_KEY`.** Ahmad adds it to the repository-root `.env` himself.
   Never paste it into a chat, a command line, a commit or a config file.
2. **`var/inference.json`.** Copy it from `config/inference.example.json` and fill in:
   - the confirmed model slug (GPT-6.1 Sol if the issued key can use it);
   - the approved serving provider and its `supported_parameters`;
   - the current Decimal prices and the date they were copied.

   All of these come from the provider's endpoint page on OpenRouter.
3. **The shared ledger.** It is on Hasan's PC today. Run on that PC, in a shell
   prepared with `scripts/Use-LocalAccounting.ps1`, or after the ledger moves to the
   hosted PostgreSQL described in [local accounting](local-accounting.md).
4. **The application database.** Set `DATABASE_URL`, use the same database for the
   API and the worker, run `alembic upgrade head`, and import the directory with
   `python -m app.directory import-seeds`.

**How it stays inside the caps.**

- Run at most one search and one stored-evidence repeat.
- The run itself refuses any call that does not fit (above).
- Expected live model calls with Class Cellulars: one or two orchestrator plans and
  one or two critiques. The writer calls the model only when an offer is verified.
- Worst case reserved per run: the configured role reservation times four, plus one
  writer reservation, all within USD 1.

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
   .venv/Scripts/python.exe -m app.run_worker --controller app.buyer_flow:run
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

   Post the summary on #75. Include the run ID, the date, the status, at least one
   validated model response with its reported model and serving provider, the tokens,
   latency and spend, and any missing metadata or uncertain billing. Then tick only
   the boxes the run actually demonstrated.
7. Stop both processes (Ctrl+C). The ledger keeps the run's record.
