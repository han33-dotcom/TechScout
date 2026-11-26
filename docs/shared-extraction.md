# Shared page extraction and managed fallback

`read_page` connects the source gate, shared job accounting, capture contracts and
extraction. Dedicated parsers take precedence. Otherwise `extract_structured`
reads schema.org Product/Offer JSON-LD, including graphs and explicitly selected
variants. Local extraction windows count against active job time. A successful
HTML shell with no supported facts may use one explicitly configured Firecrawl
fallback; a direct failure can also use one fallback under the guards below.

`PageProfile` binds a reviewed source origin and exact source SKU to the resource
key. Product/variant identity must be supplied from the identity workflow; text
similarity or a page's default variant does not establish that mapping. The shared
extractor supports price/currency, online stock, condition, color/model, RAM/storage
properties and text from a reviewed policy element. Aggregate offers, absent
values, unsupported formats and preorders do not become known price/stock facts.
Duplicate JSON keys are rejected; contradictory repeated facts become conflicts.

Each field carries the source, listing/variant scope, actual excerpt, HTML-block
JSON locator, body hash, observation time and processing versions. Online stock is
variant-scoped and cannot satisfy pickup branch stock. Policy text is source-scoped;
it does not automatically establish zero tax/shipping, item eligibility, or a
guaranteed deadline. Purchasability and missing mandatory costs remain separate
evidence requirements. Raw corpus capture cannot carry runtime verification;
runtime extraction may carry the current RunReference for downstream verification.

The parser runs no JavaScript, model tools, page-supplied instructions or callbacks.
Only controller-supplied dedicated parsers are executable. Page content never
changes buyer constraints, provider parameters, source capabilities or budgets.

## Firecrawl boundary

The [direct Firecrawl v2 scrape endpoint](https://docs.firecrawl.dev/api-reference/endpoint/scrape)
receives one admitted, public target. We request raw HTML, a 10-second scrape
timeout, fresh retrieval (`maxAge: 0`), no cache storage, basic proxy mode, TLS
verification, and no document parsers or browser actions. The provider transport
limits response bytes and time; decoded HTML must also fit the existing 2 MiB
source-body cap. There is no automatic proxy escalation, crawl or retry.

Before dispatch and again after response, the gate checks current admission,
release selection, endpoint scope/capability version and public DNS. Returned
source URL and final URL must both equal the exact approved target, with HTTP 200,
HTML content, no reported cache hit and no partial-output warning. Redirected or
missing provenance is rejected. The managed service performs its own network
fetch: local DNS preflight does not pin its remote resolver or expose its internal
subrequests. Its exact-target/provenance and fresh-scrape contract is the managed
provider trust boundary; no claim of a locally observed redirect chain is made.

Every fallback reserves one paid retrieval and the shared source slot before
dispatch. Direct retries and fallback are separate ledger attempts. Unknown usage
retains the entire configured quote; reported overcharges block the campaign.
Configure a positive Decimal USD/credit rate, or `included` for verified free quota
with enough credits and a hard stop before paid overages. Both use the same call and source limits.
An admission/budget refusal, unsafe target, rate limit, authentication requirement
or explicit 404/410 cannot be bypassed with fallback. A registered public target
returning a generic 403 can use the explicitly enabled managed path. No credentials,
retailer accounts, checkout interactions or seller messages are supplied.

## Per-source acceptance and remaining gaps

[Source profiles](../config/shared-extraction-profiles.json) cover **PC and Parts**
(WooCommerce hint) and **Abed Tahan** (Shopify hint), beyond the three dedicated
MVP parsers. Both use the same deterministic metadata extractor. Synthetic offline
fixtures demonstrate selected variants, exact Decimal prices, stock, condition,
RAM/storage and policy text, and persist evidence through the corpus. The second
fixture includes a cheaper sibling SKU and injected instructions; neither changes
the selected variant or execution policy.

These are fixture-validated parser profiles, **not live supported-source or
qualifying-offer claims**. The profiles record live SKU/metadata mapping and
site-specific gaps: item-specific taxes/shipping, delivery availability, branch
stock, purchasability and guarantees. Prior audit observations remain separate.
The profile file grants no technical admission or source activation.

`verified_price_order` accepts only fully validated VerifiedOffer objects for one
canonical variant and currency. With supplementary synthetic route, purchasability
and cost evidence, both shared-extracted retailer offers enter that comparison
alongside a dedicated-parser control. Ordering depends on verified payable total
and stable offer ID, never parser type. This is a comparison helper, not the future
multi-product recommendation/ranking policy. Unsupported leads cannot enter it.

Ordinary validation uses offline HTML and HTTP transports. No live Firecrawl call,
paid extraction or retailer activation was performed. Production rollout must
configure credentials/rates and validate current per-source fields with bounded,
recorded live observations before claiming supported coverage.
