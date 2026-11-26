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
is renewed while executing. Active work has 120 seconds total across clarification
resumes; queued and human-wait time do not count. Poll/start/claim/startup recover
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
