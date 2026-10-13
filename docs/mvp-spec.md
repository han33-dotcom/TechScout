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
limits. URL validation is structural; DNS, redirects and HTTP budgets are Task 4.

Tool results and source coverage distinguish ok, partial and error with consistent
error records; tool results also include measured duration. Run results carry up to three verified recommendations, unresolved/rejected
reasons, source coverage, usage and stop reason. Verification timestamps must fall
inside the run. An empty recommendation list may be an explicit abstention or partial
result; a failed/cancelled run cannot masquerade as completed recommendations.

JSON examples are in `tests/fixtures/contracts.json`. Future tasks must retain this
shape or deliberately update its version, documentation and affected consumers.
