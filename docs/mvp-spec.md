# Tech Scout MVP contract v1

For the five-category buyer experience, see the [buyer product contract](buyer-product-contract.md). The account and checkout exclusions below describe the legacy MVP.

Existing stores, CLI runs and stored records use this version. New consumers can use the [full-product v2 contracts](full-product-contracts.md). Both versions have explicit validation entry points.

Task 2 defines the shared data format; the shopping workflow built on it is described
under [Shopping workflow](#shopping-workflow). The first workflow is exact **new smartphones in Lebanon**, with
pickup or domestic delivery. The current product covers five electronics categories in the same domestic market. Public website retrieval is assumed unrestricted for this project.

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
aware timestamps. The store readers resolve calendar and working-day claims; dispatch claims
and cutoffs are not resolved, because no admitted store publishes one.

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
retrieval time, response hash and pointer or excerpt (some excerpts are locators the store
readers write, not quotes; see Store readers). References are observations,
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
`Offer.offer_id` is free text here, but the run controller expects the store readers'
`source:product:variant` form (see Store readers).

Cost states are additional, included, unknown and not_applicable. Additional costs
need a money amount. Included components can omit a separately quoted amount. Both
require evidence. Unknown/not_applicable states have no amount and carry a reason.
Deduplication (MVP 07, `app/identity.py`) and cost calculation (MVP 09, `app/costs.py`)
are code, not model behavior.

`SourcePolicy` retains Task 1's `id`, `origin`, `status`, `admission`,
`allowed_endpoints` and `limits`. `admission` is technical review metadata, not merchant
permission. Active entries require a review, scoped HTTPS endpoints and positive
limits. URL validation here is structural. The Task 4 source gate (`app/sources.py`) sends
only GETs, and only to a URL that exactly matches an `allowed_endpoints` entry (no
templates, so a new page or query needs a new entry) of a source that is active and listed
in `live_release.admitted_source_ids`, re-read again after any wait. Just before sending,
every request is checked again against the per-run cap, minute limit and concurrency read
last; one that no longer fits, for example because they were lowered meanwhile, is refused,
not sent. Its minute is counted back from the actual send, so requests sent while it
waited or stalled count too. An endpoint whose path
or query names a cart, checkout, account, login, admin or form route, or `_method`, disables
the source (`ACTION_PATH_DENYLIST`). On every request it checks DNS answers, redirects (at
most 3, each to an allowed endpoint), reply framing, size (2 MiB) and encoding (none, or
gzip at most twice with each stream ending exactly at its trailer), per-source limits and
the run budget. Every network wait in an attempt ends by a 10-second deadline; only the DNS
lookup and the operating system's certificate check are bounded by the operating system
instead. A reservation is released, not committed, when nothing was sent. Per-source limits,
cool-downs and per-run counts live in one limiter that every gate in the process shares
unless given its own (a gate given another clock, as in tests, needs its own); they are not
shared between processes, so the MVP runs one worker, and two command-line searches running at
once each keep their own. A run's per-run counts are dropped
once it has asked no store for an hour (`RUN_COUNT_TTL_S`), so the limiter does not grow
with every run. That is far longer than the 120-second run cap, which the run budget
(MVP 11, `app/runs.py`) enforces: a run's budget refuses requests past its deadline and is
closed when the run ends, so a finished run never asks again. The gate ignores proxy settings, but
on Linux OpenSSL still reads `SSL_CERT_FILE` and `SSL_CERT_DIR`, so the process environment
must be trusted.

Tool results and source coverage distinguish ok, partial and error with consistent
error records; tool results also include measured duration. Run results carry up to three verified recommendations, unresolved/rejected
reasons, source coverage, usage and stop reason. Verification timestamps must fall
inside the run. An empty recommendation list may be an explicit abstention or partial
result; a failed/cancelled run cannot masquerade as completed recommendations.

## Shopping workflow

This section covers MVP 05-09, 11-12 and the final live re-check (MVP 13, issue #70). Indexed
discovery (MVP 10) is not implemented: there is no `app/retrieval.py`, offer index or
refresh job, and the retrieve stage reads one configured iPhone 17 product per store instead.
A request for another model in the device table (for example iPhone 17 Pro or iPhone Air) reads
nothing: every store reports ok with 0 candidates, and the run abstains with 'no open evidence
gaps remain' ('investigation is disabled for this run' with no planner turns). A model outside the
table still reads the iPhone 17 products, which then fail or leave open the model check.

One search runs as fixed stages in `app/runs.py`: clarify, retrieve, assess, investigate,
verify and results. Code decides eligibility, cost, ranking and every limit; the planner
only picks the next evidence action from a menu the run controller builds. Real store
facts come from the source gate; nothing is bought, reserved or messaged.

### Store readers (MVP 05-06)

`app/adapters/` turns gate replies into version 1 offers with evidence. Readers never open
connections: each call gets a fetch bound to one run's budget and its own store, and returns
the raw replies to store before the evidence that cites them. Class Cellulars, Mojitech and
Beytech share a WooCommerce reader (`woocommerce.py`): the Store API slug query gives the
product, its declared minor unit and one variation record, and the product page's variation
form gives every variation's price, stock and purchasability. A Store API variation record's
price, `is_in_stock` and `is_purchasable` must agree with the page, or the fact is a conflict.
A variation on backorder is never in stock online: `online_stock` stays unknown with 'variation
is on backorder', or 'variation may be on backorder' when backorders are allowed and the page shows
no in-stock line. Each variation becomes one offer with variant-scoped evidence; a parent record
is never an offer. A variation option named Model must name exactly one known model and agree
with the product name, or `variant.model` is unknown or a conflict.

Offer IDs are `source:product:variant` (`app/adapters/base.offer_id`), each part a plain
identifier matching `[A-Za-z0-9][A-Za-z0-9_.-]{0,63}`. The contract field is free text, but the
run controller relies on this form: it never shows the planner a gap on an offer with any other
ID (see Run control).

Zero prices, too many decimals, schema changes, missing prices and AJAX-only pages stay unknown
or give `INVALID_SCHEMA`; they never become comparable offers. A Store API variation record,
an entry in the Store API variation list, or an option name repeated with a different value is
`INVALID_SCHEMA`. On the product page, a variation-form entry repeated with a different value
makes the differing fact a conflict. Identical repeats merge in both places. For pickup, a reader lists only the store's configured branches in the
requested city (only the named one, when the request names a branch), each with unknown stock.
Mojitech and Beytech have no configured branch, so their pickup gap is `pickup_stock`, not
`pickup_stock.<branch_id>.stock`.

Store-wide pages for Class Cellulars (FAQ) and Mojitech (refund and returns) become policy facts
that `apply_policy` binds to delivery offers, keeping a conflict when a policy disagrees with the
product page. An unknown that the product page left open with a cited statement (it accepts
cash and card, say) stays unknown, citing both, with the conflict reason. The Class FAQ and the
Mojitech returns page speak to delivery orders only: they leave a pickup offer unchanged, and a
pickup run is never offered them (`policy_topics_for`). A configured policy page in which the
reader recognizes no statement is a partial read with `INVALID_SCHEMA` 'policy page states
nothing the reader recognizes'. Beytech's warranty and exchange pages are read only when
`fetch_policy` is called directly: they settle no offer field, so `apply_policy` leaves offers
unchanged and a run never fetches them.

Store text never reaches a reason: every reason is fixed wording. JSON replies are cited by JSON
pointer, and page statements by their store text as the excerpt. Values the page holds as
escaped JSON (the variation form and JSON-LD) cannot be quoted as written, so their excerpt is a
locator the reader writes with the parsed value, such as `variation 20648 display_price 1030` or
`JSON-LD offer valueAddedTaxIncluded true`, and the evidence ID's tag (`item0`, `jsonld`) gives
its position. Such an excerpt is not a quote and is not found in the snapshot. What each store
publishes, and what it does not (no store states device condition), is in [source
coverage](source-coverage.md). Test fixtures are minimized from captured public pages; see
`tests/fixtures/stores/PROVENANCE.md`.

### Variant identity (MVP 07)

`app/identity.py` canonicalizes manufacturer, model and color with our own tables
(`app/devices.py` with `MODEL_ALIASES` and `identity.COLOR_ALIASES`;
'bleu' is Blue, 'iPhone 17 Air' is the iPhone Air; unmapped colors become title case) without
changing any fact's state or evidence. A '+' attached to a model reads as Plus ('iPhone 16+' and
'iPhone 16+128GB' are the iPhone 16 Plus); a spaced '+' or one joining another item ('+AirPods',
'+20W Adapter') is a bundle and leaves the model as it is. A known model followed by a qualifier
word ('Pro-Max', 'PM', 'Plus', 'Mini', 'Ultra', a stray 'e') is not read as that model, so the
model stays unknown. Storage sizes in a
product name use 1 TB = 1024 GB, so a request for a 1 TB phone says `storage_gb` 1024; decimal
sizes ('0.25TB') and digit-grouped sizes ('1,024GB') are not read. Duplicate offers are keyed by
source, product and variant ID; a SKU is never a key, so variants sharing a SKU stay separate.
Repeated records keep the latest observation of the phone; disagreeing identity facts become
conflicts carrying both evidence records. A link such as a permalink naming another color only
adds a fixed-wording warning; it never changes a fact. A run does not record these warnings yet.

### Eligibility (MVP 08)

`check_eligibility(request, offer)` in `app/eligibility.py` runs fixed checks (fulfillment
route, condition, manufacturer, model, storage, color, SIM, region, purchasable, stock,
price, payment basis, warranty, deadline, market). Each passes, fails or stays unresolved;
conflicts and missing required facts are unresolved, never passes. Every unresolved check
names the offer field it needs as an evidence gap with a fixed reason. Online stock never
proves branch stock, and an arrival estimate never satisfies a guaranteed deadline.

Some known values stay unresolved because they do not show the answer. When the buyer names a
finish ('Mist Blue', 'Sage') and the store states only its family ('Blue', 'Green'), the color
gap is 'color finish is not stated' (`identity.COLOR_FAMILIES` maps each finish to its family,
for example Mist Blue to Blue); other colors still fail. A warranty territory or region
that names the required place among other words ('Lebanon only', 'International (excluding
Lebanon)') gives 'warranty territory is not recognized' or 'region is not recognized'; other
place names still fail. 'Republic of Lebanon', 'لبنان' and 'الجمهورية اللبنانية' are spellings
of Lebanon.

Arrival claims are absolute windows built by the readers in Beirut time from calendar or working
days, using the wider of UTC+2 and UTC+3. Working days run Monday to Friday. The latest bound of
a working-day window also skips Lebanon's fixed-date official holidays (1 Jan, 6 Jan, 9 Feb,
25 Mar, 1 May, 15 Aug, 22 Nov, 25 Dec); the earliest bound does not, so a holiday can make a
deadline harder to pass but never causes a fail. Movable religious holidays and store dispatch
cutoffs are not modelled, so a window can still end too early around them. A window that starts
before the deadline but ends after it is unresolved ('delivery window extends past the
deadline', or the pickup equivalent), not failed, because its widened latest bound is not the
end the store promised. A window that cannot start before the deadline fails.

### Costs (MVP 09)

`calculate_cost(request, offer, *, fx=(), now=None)` in `app/costs.py` returns a verified total
only when the price and every applying charge (VAT, delivery, payment surcharge, other) are
settled; otherwise it returns the known subtotal, fixed reasons and evidence gaps. Arithmetic
is exact Decimal; a conversion that needs rounding rounds up, so rounding cannot admit an
over-budget offer. Included VAT is not added twice. Promotion conditions use a small grammar
(`min_subtotal:<CUR>:<amount>`, `city:<name>`, `payment:<cash|card>`); anything else stays
unresolved. Conflicting shipping terms, expired quotes and any listed unknown charge keep the
total unresolved. A price fact has no expiry of its own, so an expired device quote that repeats
the offer price and applies or might apply (only a failing condition rules it out) ends that price
('device quote has expired', gap `costs.device`), unless the price was observed after the quote
ended. A lapsed price is never replaced by a term for another amount; the device line stays open.

Each cost kind is one charge per offer: its components restate it or are alternatives keyed by
condition. A conditional term that adds nothing (included, not applicable or an additional zero)
for a payment surcharge or other charge is a waiver: when its condition fails, the ordinary
charge is unpublished, so the total stays unresolved (`published <kind> terms do not cover this
order`). The exception is a waiver whose conditions include all of a set-aside positive
surcharge's conditions: it can only waive that published surcharge, so it adds nothing. Only a
positive surcharge whose condition fails adds zero. A reader that finds several
mandatory fees must list them as one `other` component carrying their sum and every fee's
evidence. No current reader lists an `other` charge. Separately
listed nonzero `other` charges are never merged: equal amounts keep the total unresolved
('several other charges are listed separately'), unequal ones are 'conflicting other terms'.

Pickup totals always exclude travel and say so in `basis`. A buyer-supplied travel amount is not
supported yet (ShoppingRequest v1 has no field for it), so a travel component is never added.
A budget in another currency needs a timestamped exchange rate no older than 24 hours. Runs
supply no exchange rates (see Run control), so this works only when `calculate_cost` is called
directly.

### Run control, verification and planner

The legacy CLI reserves time and calls before dispatch. It records each action and its outcome. Interrupts, budget limits, source failures and storage errors have different terminal states. Inspect the stored stop reason and source coverage when reproducing a result. [CLI setup and exit codes](local-development.md#legacy-cli)

Recheck the selected variant’s price, stock and applicable terms before emitting a verified result. If a changed or missing fact invalidates the candidate, preserve the reason. The optional planner can choose permitted investigations within the remaining budget. Its output still passes through deterministic eligibility and cost checks.

The [versioned v1 implementation reference](https://github.com/han33-dotcom/TechScout/blob/9006e2ac8557003c958c31f729371ccb8e160b9a/docs/mvp-spec.md#run-control-budgets-and-traces-mvp-11) preserves the full timing, recovery, final-verification and planner contracts and examples.

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
`blocked:method` (reserved: reads only send GET, and a registry entry allowing another
method disables the source; the one reviewed POST is the delivery quote in
[delivery-quotes.md](delivery-quotes.md), refused as `blocked:url`), `blocked:dns` (a resolved address is not public),
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
