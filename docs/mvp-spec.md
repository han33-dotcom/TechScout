# Tech Scout MVP contract v1

Task 2 defines the shared data format. It does not retrieve, compare or recommend
products yet. The first workflow is exact **new smartphones in Lebanon**, with
pickup or domestic delivery. Wider electronics and international comparison remain
the product vision. Public website retrieval is assumed unrestricted for this project.

The buyer specifies manufacturer/model, storage, allowed colors, SIM configuration,
regions, budget/currency, payment basis and city. `any` explicitly means no preference
for colors, SIM or regions; omission does not silently invent a preference. Warranty
requirements and deadlines are supplied explicitly, or set to `null` when not required.
The buyer completes the purchase. Checkout, accounts, seller messaging, used devices,
open-ended device recommendations and verified import ranking are outside this release.

Pickup uses the final store payable amount, excluding travel unless supplied later.
Delivery uses the final amount to receive the device, including mandatory charges.
Unknown charges remain unresolved; they never become zero. Published online stock is
separate from observed stock at a named pickup branch. Stock observations are not reservations.
Arrival estimates cannot satisfy a guarantee requirement. Arrival windows use absolute
aware timestamps; adapters later resolve dispatch/business-day/calendar claims.

## Implementation

Python 3.10+; install `python -m pip install -r requirements.txt`, then run
`python -m unittest discover -s tests -v` from the repository root.
`app/contracts.py` uses Pydantic models to validate dictionaries/JSON and export JSON
schemas. Money is a finite nonnegative Decimal serialized as a decimal string, never
a binary float. Observation and verification timestamps normalize to UTC.

The eight public contracts are `ShoppingRequest`, `SourcePolicy`, `Variant`, `Offer`,
`CostComponent`, `Evidence`, `ToolResult` and `RunResult`. Each carries `schema_version: 1`.
Input fields not in the schema are rejected, except unrelated registry metadata on
`SourcePolicy`, which intentionally consumes the operational subset of Task 1's entry.

| Contract | What teammates exchange |
|---|---|
| ShoppingRequest | Exact device, buyer constraints, money, location and payment/deadline basis |
| SourcePolicy | Selected source, public endpoints, review metadata and operating limits |
| Variant | Actual retailer product/variant IDs, normalized observations and raw attributes |
| Offer | Variant, price/payment basis, stock, warranty, fulfillment, costs and evidence |
| CostComponent | Charge type, inclusion state, amount/reason, conditions and quote expiry |
| Evidence | Fact field and scope, source URLs, UTC time, response hash and pointer/excerpt |
| ToolResult | Typed success/partial/error outcome, JSON data, evidence IDs and duration |
| RunResult | Recommendations, unresolved/rejected reasons, source coverage, usage and stop reason |

Supporting `Fact[T]` values are known (one value with evidence references), unknown
(null value and reason), or conflict (distinct alternatives and reason). Raw retailer
attributes, product/variant IDs and native price/unit metadata remain available.
Evidence records bind source, actual variant/branch/route, field path, origin/final URL,
retrieval time, response hash and pointer or excerpt. References are observations,
not proof of truth; later eligibility/final verification checks resolve them.
Offer references must match their evidence's field path. Use `price`, `payment_basis`,
`variant.color`, `warranty.territory`, `costs.delivery`, or the corresponding field.
Pickup stock uses `pickup_stock.<branch_id>.stock` with matching branch-scoped evidence
for the actual product and variant. Manufacturer/model evidence must identify the product;
generic source-level claims cannot prove device identity.
Variant-specific price, condition, purchasability, online stock and variant attributes
require actual variant-scoped evidence. Cash/card offer price terms are an explicit
fact; unpublished terms remain unknown. Manufacturer/model existence and free-text
interpretation are later identity/planning work, beyond obvious broad-input rejection.

Cost states are additional, included, unknown and not_applicable. Additional costs
need a money amount. Included components can omit a separately quoted amount. Both
require evidence. Unknown/not_applicable states have no amount and carry a reason.
Cost calculation and deduplication are Task 9, not model behavior.

`SourcePolicy` retains Task 1's `id`, `origin`, `status`, `admission`,
`allowed_endpoints` and `limits`. `admission` is technical review metadata, not merchant
permission. Active entries require a review, scoped HTTPS endpoints and positive
limits. URL validation here is structural. The Task 4 source gate (`app/sources.py`) sends
only GETs, and only to a URL that exactly matches an `allowed_endpoints` entry (no
templates, so a new page or query needs a new entry) of a source that is active and listed
in `live_release.admitted_source_ids`, re-read again after any wait (a request that no
longer fits the per-run cap, minute limit or concurrency read then, for example because
they were lowered meanwhile, is refused, not sent; its minute is counted back from the
actual send, so requests sent while it waited count too). An endpoint whose path
or query names a cart, checkout, account, login, admin or form route, or `_method`, disables
the source (`ACTION_PATH_DENYLIST`). On every request it checks DNS answers, redirects (at
most 3, each to an allowed endpoint), reply framing, size (2 MiB) and encoding (none, or
gzip at most twice with each stream ending exactly at its trailer), per-source limits and
the run budget. Every network wait in an attempt ends by a 10-second deadline; only the DNS
lookup and the operating system's certificate check are bounded by the operating system
instead. A reservation is released, not committed, when nothing was sent. Per-source limits,
cool-downs and per-run counts live in one limiter that every gate in the process shares
unless given its own (a gate given another clock, as in tests, needs its own); they are not
shared between processes, so the MVP runs one worker. A run's per-run counts are dropped
once it has asked no store for an hour (`RUN_COUNT_TTL_S`), so the limiter does not grow
with every run. That is far longer than the 120-second run cap MVP 11 plans, but nothing
enforces that cap yet, so until then a run that pauses for an hour starts its counts
again. The gate ignores proxy settings, but
on Linux OpenSSL still reads `SSL_CERT_FILE` and `SSL_CERT_DIR`, so the process environment
must be trusted.

Tool results and source coverage distinguish ok, partial and error with consistent
error records; tool results also include measured duration. Run results carry up to three verified recommendations, unresolved/rejected
reasons, source coverage, usage and stop reason. Verification timestamps must fall
inside the run. An empty recommendation list may be an explicit abstention or partial
result; a failed/cancelled run cannot masquerade as completed recommendations.

## Tool error codes

`ToolError.code` is a closed list. `retryable` says whether the same request may be sent
again within the run; only the source gate (Task 4) retries, and callers must not re-send.

| Code | Meaning | Usually retryable |
|---|---|---|
| `TIMEOUT` | A connect, read or overall deadline expired | Yes |
| `RATE_LIMIT` | The store answered 429, or the gate's own per-source limit is full | Yes |
| `NETWORK_ERROR` | Connection failure, reset, truncated reply, a 502, 503 or 504 reply, failed DNS lookup or TLS verification | Yes, except TLS |
| `SOURCE_DISABLED` | The source is unknown, not active, not selected or misconfigured in the registry | No |
| `BLOCKED` | The gate refused the request or its reply on safety policy | No (enforced) |
| `HTTP_ERROR` | A permanent HTTP status: any non-2xx reply, including 500, except 429 (`RATE_LIMIT`), 502, 503 and 504 (`NETWORK_ERROR`) and redirects (followed, or `BLOCKED`) | No (enforced) |
| `INVALID_SCHEMA` | The reply arrived but is malformed, has no length framing or is not the expected shape | No |
| `BUDGET_EXHAUSTED` | The source's per-run cap or the run budget has no room left | No |
| `FIELD_CONFLICT` | Fetched facts disagree | No |
| `NO_PRICE` | No usable price was published | No |

`BLOCKED` messages start with a fixed reason: `blocked:url` (not an allow-listed endpoint),
`blocked:method` (reserved: the gate only sends GET, and a registry entry allowing another
method disables the source), `blocked:dns` (a resolved address is not public),
`blocked:redirect` (unsafe or too many redirects), `blocked:size` (reply over the size cap)
or `blocked:encoding` (unsupported content encoding). `HTTP_ERROR` messages start with
`http:<status>`, the three-digit status code. Each prefix is followed by a space or ends the
message. The contract enforces these rules: it rejects a `BLOCKED` or `HTTP_ERROR` record
whose message does not start with its documented prefix (for example `blocked:urlx`,
`http:4040`, or an `http:` prefix on a `BLOCKED` record) or whose `retryable` is true.
Other codes keep free-text messages and their own `retryable` value.

`BLOCKED` and `HTTP_ERROR` were added within `schema_version: 1`, together with these
checks. Every stored version 1 result stays valid: until this change the contract rejected
both codes, so no stored record can contain them, and the other codes are unchanged.

JSON examples are in `tests/fixtures/contracts.json`. Future tasks must retain this
shape or deliberately update its version, documentation and affected consumers.
