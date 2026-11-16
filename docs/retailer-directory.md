# Persistent retailer directory

Issue #86 adds `app.directory.Directory`, migration 0006 and the versioned
`DirectoryRecord` envelope (`directory_schema_version: 1`). It embeds the existing
v2 `RetailerDirectoryEntry` unchanged. Existing fixture readers remain compatible;
`DirectoryPort.get` implements the async shared directory interface and returns that
entry. The synchronous storage API returns the richer record.

After database migrations, import the checked-in research without any network calls:

```text
python -m alembic upgrade head
python -m app.directory import-seeds
```

The importer adds 20 Lebanese audit/lead entries and six confirmed international
planning targets: Microless UAE and B&H US for direct-delivery investigation, Amazon
US for forwarding investigation, MyUS first and Stackry backup for forwarding, and
Shop & Ship as a candidate. These roles do not establish item delivery, price, stock,
route acceptance or qualifying offers. Imports are idempotent, older updates cannot
replace newer data, and equal-time conflicting revisions are rejected.

Canonical directory/seller IDs, marketplace membership, origins, aliases, categories,
brands, purchase markets, routes, methods, capability references and dated policy
links persist together. Alias lookup fails explicitly on ambiguity: two sellers on
one marketplace must not be collapsed. Source country is separate from purchase
markets and fulfillment role. A US evidence source can support a Lebanese offer;
runtime selection of foreign purchase markets still requires explicit opt-in.

Coverage has distinct discovered, category retrieval-tested and qualifying-offer
levels. Tested/qualifying levels require dated evidence references. The audit's
source-wide attempts do not prove every category was tested, so the importer keeps
those attempts and failures separately and leaves per-category tested/qualifying
flags unset. Policy links retain their audit dates and uncertainty summaries; they
are research references, not newly invented offer evidence. Nothing is promoted to
current-run qualification by directory import.

`Directory.select(DirectoryQuery(...))` filters category/purchase market in SQL,
examines at most 200 entries, and returns at most 20 selections. Research priorities
can explain category, price-band and configuration gaps, competing sellers, measured
HTTP outcomes and recorded useful evidence. These priorities never enter purchase
ranking and are not trust/reputation scores. Background collection eligibility is
separate from runtime selection. Leads can be inspected with `include_unconfigured`;
they return no permitted URLs until technical source configuration is valid.

Every selection rereads the technical registry. Active status, reviewed endpoint
scope, positive workload limits, matching origins and current capability versions
are required. Directory membership cannot activate a source. Returned URLs are
selection hints: the execution controller and `SourceGate` must recheck them at fetch
time, including revocation, runtime budgets and redirect rules. No permission-email
or teammate-review gate is introduced.

Tests use synthetic registry data and the checked-in audit. They demonstrate aliases,
marketplace separation, opt-in, disabled/missing capability handling, truthful
coverage, idempotent import and bounded category selection on SQLite/PostgreSQL.
The directory does not itself start background ingestion or buyer research.
