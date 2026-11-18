# Automatic retailer onboarding

Issue #99 adds `app/onboarding.py` and migration 0009. A background agent turns retailer
leads into active source capabilities without a teammate editing `config/sources.json`.
The agent only proposes. Fixed backend rules decide, and the existing source gate,
shared extraction and shared ledger do all fetching, parsing and accounting.

## Flow

1. **Intake.** `OnboardingQueue` accepts leads from Tavily (`enqueue_tavily`), the retailer
   directory (`enqueue_directory`) and buyer searches (`enqueue_buyer`, or `enqueue_reports` for
   research reports). A lead must be a
   canonical public HTTPS URL that the gate's own endpoint rule accepts: no IP literal, port,
   credentials, percent-encoding or action path. Leads merge by shop domain (one leading
   `www.` is ignored). A merged lead keeps every channel, category, market and up to ten
   candidate URLs. Buyer intake stores only the URL, category and market, never buyer text.
2. **Priority.** Collection priority is `100 × coverage gaps + 20 × mentions + 40 × relevance
   + 15 (buyer demand) + 10 (directory lead)`. A gap is a category and market with fewer than
   three configured directory sources. This orders background work only; it never enters
   offer ranking.
3. **Probe.** The worker leases the top due lead. The proposer names up to four sample pages
   on the lead's domain. `ProbeRegistry` is a short-lived registry document (10 minutes, and
   closed after use) that admits only those exact URLs. The probe goes through the normal
   `SourceGate`, so the gate's public-IP, pinned-TLS, redirect (inside the exact targets only),
   2 MiB, timeout and workload checks apply. The optional Firecrawl fallback keeps its own
   exact-target rules.
4. **Proposal.** The proposer sees bounded page summaries: SKUs, product names, stated
   sellers and organizations. It never sees body text or descriptions. It proposes the shop
   name, category, two to four `(url, sku)` samples and the narrowest product and variant
   path scopes. `HeuristicProposer` is the deterministic default. `ModelProposer` asks any
   `ModelProvider` for the same structured output, metered on the same job, and can fall back
   to the heuristic. A budget refusal stops the attempt. Without a fallback, a model timeout
   or transport failure retries the lead, and a lasting refusal becomes an exception.
5. **Validation** (`validate_configuration`, no model):
   - At least two distinct listings, each a successful probe reply on the shop host. Distinct
     means different final URLs and different reply hashes, so a redirect between two targets
     counts once.
   - Shared extraction binds a known price, currency and stock to each proposed SKU.
   - Each receipt cites that reply's hash, URL and SKU.
   - A conflicting price or stock is rejected as an ambiguous variant.
   - Every seller a sampled offer states must be the shop. A plain-text seller counts, and an
     `@id` reference must resolve to a name on the page; an unresolved reference fails.
   - Some sampled page must state the shop's name in schema.org data.
   - The market comes from the country domain or the stated organization address, must be
     in the lead's scope, and must fit the listing currency.
   - The category comes from the lead. A sampled product whose stated category names only
     other categories rejects the proposal. The scope records whether the category rests on
     the pages or only on the lead (`category_basis`).
   - Policy text from a proposed element is kept as source-scoped evidence. Shipping,
     warranty, returns and tax stay listed as unknown terms, never zero.
   - Each operation is decided alone. `product` must cover every sample and its final URL.
     `variant` also needs a variant case: two SKUs of one page bound to separate offers.
   - A scope needs a literal path prefix, at most two required parameters and no search text.
   - `search`, `catalog`, `feed` and `sitemap` are left for a teammate.
6. **Activation.** `ActivationRegistry.activate` writes every approved capability under one
   database lock, with its evidence, samples, validated scope and the lead's recorded cost.
   - The version is a digest of the validated scope. A replay, a parallel worker or a re-run
     lead therefore records nothing twice.
   - A different scope supersedes the active version.
   - An identical scope that was suspended comes back only through a new worker attempt whose
     samples were all read after the suspension, which means a teammate reopened the lead. A
     replay of older evidence never revives it.
   - The directory record gets the canonical seller, market, tested category coverage and
     sample URLs before activation, so a crash in between leaves only a lead.

## The durable registry

`ActivationRegistry(storage)` combines a fresh read of the reviewed JSON file with active
database rows on every call. Pass it wherever a registry path is accepted:
`SourceGate(registry)`, `Directory(storage, registry_path=registry)` and
`job.source_budget(registry)`. The API and worker share it through the database.
`app.source_registry.load_registry` is the small seam that lets the gate read either form.

Onboarded sources get the reviewed engineering limits: 6 requests per minute, one at a time,
six per run. The reviewed file always wins:
- A shop with a reviewed `pending` entry is activated under that entry's ID and stands in for it.
  Directory links therefore keep working, and once a teammate reviews the entry, the reviewed
  version takes over.
- Any other shop gets an automatic ID such as `auto-cedartech-example-7834a785`. The digest
  keeps similar domains apart.
- Any other reviewed status for the shop's domain or a parent domain (active, paused,
  revoked and so on) keeps onboarding away and hides an onboarded entry.
- Only records for the ID's own domain and host are admitted.
- A directory record that already links a different reviewed source makes the lead a
  `reviewed_registry` exception.

`suspend` stops admission at the next request. It keeps the record, evidence and event
history, and turns the shop's lead into a `suspended` exception for a teammate.

`OnboardingWorker.recheck` re-reads recorded samples through the live registry and suspends
a capability that no longer validates or no longer reads within scope. It never suspends one
for:
- a transient failure or a budget refusal;
- a removed listing (404/410). When too few listings remain, it answers `needs_samples`.

## Limits, retries and failures

Each claim of a lead is its own `maintenance` ledger job, international when the lead's market
scope is. Probe fetches, local parsing windows and model calls therefore count against the
probe's source limits, the shared source leases, the daily maintenance allowance (and its
international subset) and the project total.
- A ledger refusal defers the lead without using an attempt, and stops the worker run. The
  lead waits for the next UTC day when today's maintenance or project allowance is spent or
  the campaign is stopped. A busy shared slot only pauses it for five minutes.
- A temporary failure (every probe target answered 429/5xx, timed out or dropped) retries
  after 15 minutes, then 1 hour.
- The third failed attempt becomes a `retry_limit` exception. So does a lead whose worker
  stopped three times; an expired lease is resumed by the next claim.
- The worker renews its lease before probing, before proposing and before writing. A worker
  that lost its lead stops at the next of these points. Activation checks the lease again in
  its own transaction.

Unresolved failures stay in the lead record for teammate follow-up, with a code, reason,
URLs, the gate's fetch records (status, final URL, hash, error code) and any extraction
receipts. Page text is never stored there. Commands:

```text
python -m app.onboarding exceptions
python -m app.onboarding history --source auto-cedartech-example-7834a785
python -m app.onboarding suspend --source ID --capability product-page --reason "..."
python -m app.onboarding reopen --lead cedartech.example --note "profile fixed"
python -m app.onboarding run --max-leads 3
```

`run` makes public read-only GETs to queued shops with the heuristic proposer: no paid
calls. It needs the shared accounting ledger (`TECHSCOUT_ACCOUNTING_URL`).

## Integration points

- **#88, background runner.** Schedule `OnboardingWorker.run()` and `recheck(source_id)` with
  the maintenance ledger. Do not run probes from the API process. Catalog bootstrap (#87)
  reads registries through `get_active_source`, so it can use `ActivationRegistry` too.
- **#68 and #75, buyer flow.**
  - Pass `DiscoveryReport.leads` to `enqueue_tavily`, and the orchestrator's
    `InvestigationResult.reports` to `enqueue_reports`.
  - Build buyer gates, directories and source budgets on `ActivationRegistry`, so activated
    shops appear in later searches.
  - The v1 run controller (`app/runs.py`) now reads registry objects too. It still selects
    only sources with dedicated adapters.
- **#76, full-flow demonstration.** Record one automatic activation and one teammate
  exception there.
- **Migration 0009.** If another branch also adds a 0009 revision, renumber whichever merges
  second.

## Evidence and limitations

The offline suite (`tests/test_onboarding.py`) runs on SQLite and PostgreSQL with a fake
Tavily API, a fake shop behind the real `SourceGate`, fake model providers and an invented
shop (`tests/fixtures/onboarding`). It covers:
- a Tavily lead that a buyer search queues, activates and a later buyer search uses, with
  the offer passing full-product eligibility, `VerifiedOffer` validation and
  `DiscoveryReport.qualify`;
- deduplication and priority;
- unsafe URLs, targets, redirects, private addresses, oversized replies and subdomains of a
  revoked shop;
- ambiguous variants, unknown SKUs, missing stock, too few or duplicate listings, third-party
  sellers in each JSON-LD form, a contradicting category and an unresolved market;
- blocked, transient and recovered sources, and the Firecrawl fallback;
- budget exhaustion, a stopped project and restart after an expired lease;
- replay and parallel activation, concurrent claims and workers, supersession, suspension,
  reactivation after a reopen, and recheck;
- a real-time clock, late workers, and cross-process visibility;
- the teammate commands and the v1 run controller reading a registry object.

No live retailer, Tavily, Firecrawl or model call was made. A live check would run
`python -m app.onboarding run --max-leads 1` on one real lead: a few public GETs and no paid
calls with the heuristic proposer. With `ModelProposer`, add two model calls per lead.

Known limits:
- Only schema.org JSON-LD pages fit the shared profile, so shops without it remain teammate
  exceptions.
- URLs with percent-encoding are refused by the existing gate rule.
- Catalog, search, feed and sitemap capabilities still need a teammate.
- Seller identity needs the shop's name in structured data.
