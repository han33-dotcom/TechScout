# Retailer source admission

TechScout uses reviewed public retailer endpoints. A source needs a canonical identity, review evidence, scoped endpoints and positive workload limits. The repository’s public-data policy allows retrieval without retailer outreach. [Registry](../config/sources.json) · [Coverage](source-coverage.md)

## Scoped retrieval capabilities

Review each operation and record it under `endpoint_capabilities` in the technical source
registry. A capability declares its ID and version, the source's canonical HTTPS host,
operation and response format, and the permitted path and query. The source must be
active and selected for live access, with technical review evidence and positive
workload limits. Capability IDs are unique within each source. A retailer directory
record can reference a capability, while the current registry controls dispatch.

| Field | Bounds |
|---|---|
| Operation | `search`, `product`, `variant`, `catalog`, `feed` or `sitemap` |
| Path | Ordered literal segments and optional typed segments after the first literal. Match the exact segment count and trailing slash. An empty path matches `/`. |
| Parameters | A length-limited ASCII token, an inclusive nonnegative integer range, or a finite choice list. Query values can also be length-limited ASCII search text with spaces written as `+` or `%20`. |
| Query | List every accepted key and mark optional keys explicitly. Unknown keys, duplicates, and missing required values are refused. |
| Format | `json`, `html` or `xml` as declared in the rule. XML covers sitemap and feed media types and is returned as capped bytes. Discovery code owns parsing and subsequent URL selection. |

The [offline source registry fixture](../tests/fixtures/source-capabilities.json)
shows search, variant detail, catalog pages, a feed and a sitemap. Supply the
configured reference when calling the existing gate.

~~~python
from app.source_capabilities import CapabilityRef

result = gate.fetch(
    budget, 'alpha', 'https://alpha.example/catalog/laptops?page=2&limit=10',
    expect='html', capability=CapabilityRef(capability_id='catalog-page', version='1'),
)
~~~

Call the same gate for each next page or discovered product URL using its corresponding
capability. Each redirect stays within the original rule or the original exact allowlist
and each followed hop is checked against the current registry and counted against the
same run budget. Public IP, pinned TLS, method, response size, timeout and workload
checks apply to all requests. Arbitrary URL templates, wildcard hosts, encoded paths
and known action routes are rejected. Existing `allowed_endpoints` still work for
calls without a capability reference. Live retailer registrations retain their reviewed
exact endpoints pending separate validation of broader routes.

## Workload and activation

Enforce the limits for each source and each run. A local limiter may wait briefly or return `RATE_LIMIT`. Shared ledger refusal returns `BUDGET_EXHAUSTED`. Refused requests are never sent. [Shared accounting](shared-accounting.md)

[Automatic onboarding](retailer-onboarding.md) proposes new capabilities, probes exact sample URLs and activates independently validated operations. The reviewed file and activation registry supply the current dispatch rules.

## Seller quotes

The buyer contract allows a scoped pre-order delivery quote for the chosen item, quantity and destination. A checkout-only fee stays unknown until the seller supplies it. Follow the contract for naming sellers and requesting an address. Stop before order, reservation or payment. [Buyer contract](buyer-product-contract.md) · [Quote implementation task](https://github.com/han33-dotcom/TechScout/issues/186)

The [original source review](https://github.com/han33-dotcom/TechScout/blob/9006e2ac8557003c958c31f729371ccb8e160b9a/docs/source-admission.md) records the October MVP shortlist and dated prices. [Response hashes](source-evidence.json)
