# Shared accounting and bounded research

The v1 `RunBudget` remains the local cap/tracing contract. Production default run
wiring wraps it in `AccountedRunBudget`; source attempts and model charges must
also pass the durable campaign ledger. Live embeddings require a ledger job.
Full-product role/provider clients use `Job.reserve` directly. Fake clients can
use the same interface without credentials or network calls.

## Configuration and ownership

All API processes, collection workers and developer laptops must point
`TECHSCOUT_ACCOUNTING_URL` at **one central PostgreSQL database**, with
`TECHSCOUT_ACCOUNTING_PROJECT=techscout-december-2026`. This URL is distinct from
the local corpus/database URL and has no local fallback. Apply repository migrations
to that database, then an administrator explicitly runs:

```python
from app.accounting import Ledger
Ledger.configured().provision()
```

Provisioning the same identifier is idempotent; a different identifier is refused.
Clients cannot reset the existing campaign by changing an environment variable.
Do not provision separate ledgers per laptop. Back up the database and restrict
administrative credentials; deleting/restoring accounting data can defeat any
application ledger. SQLite and injected clocks are explicit offline-test options.
Missing configuration, migrations, database access or project identity stops work.

Migration 0007 adds a single serialized campaign state row. Every transition takes
a database write lock, reads the database clock, validates all relevant scopes,
and commits atomically. It releases the lock before network work. This intentionally
simple representation fits the small bounded campaign; migrate to indexed attempt
tables before substantially expanding volume. Never prune usage to reclaim budget.

## Reservations and recovery

Use a stable `job_id` across resumes and clarification. `open_job` retains its start,
scope, cancellation and usage. Every provider attempt, explicit retry, fallback,
source probe, extraction and embedding call reserves before dispatch. There are
no automatic retries. `Charge` requires known Decimal upper bounds for provider,
gateway, payment and BYOK fees. An unknown quote is rejected. OpenRouter fallback
attempts must be separately reserved with fees; do not enable opaque gateway
fallbacks with an unbounded cost or unknown number of attempts.

An attempt has an unguessable owner token and bounded expiry. Mark it dispatched
immediately before sending. Only an unexpired unsent attempt can be released;
completed usage refunds the difference from its bound once. Unknown billed usage,
lost responses, process crashes and expired leases retain their full allowance.
`recover()` expires ownership and frees concurrency slots, never money or attempts.
Late owners cannot refund or dispatch. Do not replay an expired attempt: a retry
needs a new reservation. Provider clients must enforce their declared timeout and
stop reading/working at cancellation. Budget refusals preserve previously stored
partial results. A reported overrun is recorded and blocks the entire campaign
for explicit operator reconciliation; it is never silently discarded.

`credit_purchase(receipt_id, credit_usd, fee_usd)` records funding idempotently.
Credit principal is not spending twice: subsequent provider consumption is charged,
while non-refundable purchase fees count once at purchase. The receipt is an
accounting record, not a payment operation. Reserve/check fees before committing
any purchase; no code here buys credits or configures provider billing.

## Fixed limits

| Scope | Fetch attempts | Paid calls | Active worker time | Spend |
| --- | ---: | ---: | ---: | ---: |
| Entire campaign | — | — | — | $500 |
| Initial collection, all jobs/resumes | 3,000 | 200 | 3 hours | $50 |
| International subset | 1,000 | 60 | 1 hour | $15 |
| Maintenance per UTC day | 300 | 30 | 30 minutes | $3 |
| International daily subset | 100 | 10 | 15 minutes | $1 |

All applicable scopes must fit, including in-flight reservations. International
work also consumes the parent allowance; opt-in is explicit. Daily jobs cannot
cross into the next UTC day under the same ID. New daily jobs share that day's
aggregate allowance; bootstrap has no date-based reset.

Active time is conservatively measured as summed worker time, not merely elapsed
wall time across parallel workers. Wrap local processing in `work` reservations
as well as external calls; research worker leases cover their whole role execution.
Nested calls may count overlapping time twice, which stops earlier, never later.
Persist partial corpus results incrementally. Scheduling, queue idle time and pauses
are outside active windows. A resumed job starts new windows without resetting sums.

Provisional buyer caps are $1, 40 external calls and 120 seconds total. Exploration
can use $0.75, 30 calls and the first 90 seconds. The remaining $0.25, 10 calls and
30 seconds serve final verification/writing; `enter_final_phase()` unlocks them
inside the same deadline. Background work has at most two in-flight slots and
cannot consume the final $1 of campaign funds. Retailer concurrency, per-minute
and per-job request caps apply across API and ingestion processes using the same
registry limits; the SourceGate still revalidates endpoint admission and network
safety. A busy source is refused rather than silently bypassed.

## Role execution and integration

`research(job, clients, request)` dispatches one to three independent role clients.
Each receives the shared job and a cooperative stop event. Worker ownership is
durable, so another process cannot start a fourth role for the same run. Recursive
fan-out is refused. Timeout cancels future dispatch, returns completed partial
results and does not wait indefinitely for an uncooperative client. Role code must
honor cancellation; this does not forcibly kill arbitrary Python threads.

`Ledger.snapshot()` is the audit/reporting interface: job scope, charges including
fees, reservation owner/status and usage are durable. Existing v1 traces continue
to report local usage. No API keys, buyer text or retailer page text enters this
ledger. The legacy Anthropic planner preserves its existing conservative bundled
fallback estimate; new provider integrations must expose individual attempts.

Offline acceptance covers process races, expiry/stale ownership, every collection
ceiling, shared source leases, UTC rollover, clarification/resume, three actual
parallel fake roles, cancellation, fees and prepaid credit reconciliation. Live
roles and central service provisioning remain deployment/integration work; no paid
service or retailer is contacted by these tests.
