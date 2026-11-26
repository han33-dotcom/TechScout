# Owned buyer API and worker lifecycle

The FastAPI handlers in `app/buyer_api.py` call `BuyerRunService` directly. The
service owns database transactions; it never waits for a model or retailer call.
Migration 0008 adds private sessions and durable run snapshots, separate from the
public evidence corpus. PostgreSQL and SQLite use a database guard to serialize
short admission/state transitions across processes.

## Browser contract

First call `GET /api/session`. It returns a CSRF token and sets a random HttpOnly,
SameSite=Strict session cookie. Only its SHA-256 hash is stored. Sessions expire
after 24 hours; recovery removes their private run rows. All run reads and writes
require the owner session. A different session sees 404, not another buyer's data.
Responses under `/api/` use `Cache-Control: no-store`.

The session response also names the signed-in TechScout account, or `null`.
Registration, sign-in, sign-out, private saved locations and a chat's destination
choice are described in [buyer-accounts.md](buyer-accounts.md).

Mutations require both the exact browser `Origin` and `X-CSRF-Token` from the
session response. Secure cookies are the standalone backend default. For local
HTTP development export `TECHSCOUT_COOKIE_SECURE=0`; the localhost-only Compose
stack already defaults to 0. HTTPS deployments set 1 and configure trusted proxy
scheme/host forwarding. There is no cross-origin API configuration.

| Endpoint | Input | Result |
| --- | --- | --- |
| `POST /api/runs` | v2 `DomesticShoppingRequest`, new request ID, revision 1 | 202 queued `RunSnapshot` |
| `GET /api/runs/{id}` | Owner cookie | `ComparisonWorkspace`, latest revision and previous snapshot |
| `POST /api/runs/{id}/clarify` | `run`, `clarification_id`, `answer` | 202 resumed snapshot, incremented run revision |
| `POST /api/runs/{id}/cancel` | `run` | Cancelled snapshot; terminal cancellation is idempotent |
| `POST /api/runs/{id}/refine` | `run`, next-revision `request` | 202 workspace; previous run remains separate |

`run` is the complete `RunReference`, including run and request revisions. Stale
commands fail with 409. Other errors include 401 expired/missing session, 403
origin/CSRF, 404 unknown/unowned run, 413 body over 64 KiB, 422 invalid contract,
and 429 admission capacity. Clarification answers are bounded to 4,000 characters
and six per run. Interpretation of an answer belongs to the controller; storing
an answer does not pretend that constraints have already been updated.

Polls return actual queued/running/clarification/terminal state, coverage and
published candidates. Provisional evidence retains its original observation time.
Only validated current-run price/stock receipts can support recommendations;
publication also checks hard eligibility and the contract's budget/route rules.
Refinement creates a new request/run with its own execution allowance. Older
candidates remain in `previous`, never silently becoming new verified results.

## Worker and limits

Run `python -m app.run_worker --controller trusted.module:function` in a separate
process using the same database as the API. This is a backend deployment argument,
never buyer-supplied code. The callable receives `RunContext`: request, recorded
answers, remaining active seconds, cancellation and guarded publication. Ordinary
Python calls connect the controller and its roles; no internal agent HTTP service
is required.

Default admission allows one active/queued/waiting run per session, 20 globally,
and two running controllers across processes. A worker lease lasts 15 seconds and
is renewed while executing. The standard controller has 120 active seconds across clarification
resumes. The iteration 2 agent has a 300-second active allowance and the shared ledger enforces
its 120-second external-work deadline. Queue and human-wait time do not consume active time.
Poll/start/claim/startup recover
expired leases as partial, without automatic external-call retries. A local
monotonic deadline also stops publication if the database clock has not ticked.
Late callbacks after cancellation, refinement, expiry or clarification lose their
lease. Controller threads that ignore cancellation retain their process slot
until they return; they cannot accumulate unbounded local threads.

The controller must pass remaining time and cancellation through provider/tool
boundaries and use the shared accounting ledger for all spend/source reservations.
Its integration must preserve a single run resource allowance across clarification
resumes; the API active-time counter does not itself authorize paid calls. A Python
thread cannot forcibly terminate a remote request already dispatched.

Start the domestic buyer worker in the backend environment with the shared database, evidence
volume, inference configuration and ledger used by the API.

```text
python -m app.run_worker --controller app.buyer_flow:run
```

The v2 request accepts an optional `implementation` field. The UI's **Shopping assistant**
selector sends `control` (default for existing callers and saved requests) or `single_agent`.
The latter uses the original intent wording, structured requirements, refinement note and
recorded question/answer pairs on every decision. It uses the OpenRouter route and low effort
in `config/evaluation.json#/evaluation_inference`. A decision can select only server-offered
actions and verified offer IDs. The same domestic verifier calculates eligibility and costs.
The local catalog, reviewed retailer search, scoped reads and configured discovery channels
are available separately. Web queries can use the offered public product and locality terms.

For #201, inject the shared search implementation at `research.workers.domestic_tools`
(`connectors` and `search(SearchObjective, job, alternatives=()) -> SearchReport`).
Set `FlowDependencies.tavily_preflight_ok` after the common successful preflight.
`FlowDependencies.retailer_browser(request, run)` opens an isolated context manager yielding
the `BrowserSession` interface in `app.shopping_agent`. It exposes state-bound action IDs
and safe labels. Dispatch receives the selected listing, action ID, phase-limited ledger job
and remaining time. The adapter owns source scope, consent and traffic accounting and returns
the selected listing plus `ReadResult` receipts. The agent offers browser actions only while
the adapter is present. A scoped pre-order quote may supply delivery evidence. Payment
and ordering are outside this interface.

The worker can use stored listings, reviewed retailer reads and discovery to find candidates. The [experiment record](buyer-architecture-comparison.md) reports demonstrated results. The [buyer contract](buyer-product-contract.md) defines the intended comparison. The historical single-source phone controller is available as `app.buyer_flow:run_checkpoint`. [Checkpoint runbook](live-flow.md)

## React and validation

`frontend/src/api.ts` provides typed same-origin session/create/poll/clarify/cancel/
refine calls. It never automatically repeats mutations after ambiguous failures.
Use `matchesRun` and abort superseded polls when updating revision-aware UI state.
Vite already proxies `/api`; the production FastAPI SPA fallback excludes API,
evidence and asset paths so errors cannot turn into a successful HTML page.
Provider keys stay in backend environment configuration.

Generate `frontend/src/api-schema.d.ts` from the actual FastAPI OpenAPI schema:

```text
cd frontend
npm ci
cd ..
python -m app.api_types
python -m unittest discover -s tests -p test_buyer_api.py -v
cd frontend
npm run build
```

The offline API suite verifies ownership/CSRF, bounded multi-process claiming,
clarification timing, cancellation/refinement races, interrupted workers, original
candidate times, current-run publication, payload limits and SPA behavior. A schema
fingerprint fails when generated types need updating. Full CI runs the suite on
SQLite/PostgreSQL and builds/restarts/restores the local Docker stack.
