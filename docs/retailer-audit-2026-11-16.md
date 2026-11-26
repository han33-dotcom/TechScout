# Lebanese retailer audit — 16 November 2026

For [#50](https://github.com/han33-dotcom/TechScout/issues/50).
The [importable seed](../config/retailer-directory-seed.json) contains 20 directory
records: 12 priority audits and eight wider leads. The
[machine-readable audit](retailer-audit-2026-11-16.json) preserves entry points,
category coverage, identity uncertainty, policy references and 41 direct probe
results with UTC timestamps and hashes for successful replies.

## What the counts mean

All 20 are discovered candidates. Twelve received the bounded direct audit:
homepage plus a representative product or catalog route and a policy/identity
route, with six additional targeted checks. Nine priority homepages returned
200; three returned 403. Product routes also returned 429, and a 200 catalog
did not always expose usable product cards. **Zero qualifying offers were
established by this audit.** This is neither a claim that these stores have no
eligible offers nor a working adapter count.

The eight wider leads received only discovery/identity research, not the
product-and-policy retrieval audit. Retailer-owned pages verified Technodel,
iStore Plus, KAYSTORE and Microcity; Tech Zone's retailer-owned search result
identified the site but its browser open timed out. GCS, M2 and Qabass remain
[MEIT distributor leads](https://www.meit.biz/resellers/), with identity and
category verification pending. A browser error is recorded as a limitation, not
a negative reputation judgment.

A single 15-minute wider discovery sweep was bounded separately from the priority
audit. Future sweeps should add competing configurations, missing categories and
regional shops. There is no inventory ceiling or store-count integration quota.
[Competitor directories](https://lebtechlb.com/shops) supplied leads only;
none of their prices, stock or rankings became purchase evidence.

See the [full dated audit](https://github.com/han33-dotcom/TechScout/blob/9006e2ac8557003c958c31f729371ccb8e160b9a/docs/retailer-audit-2026-11-16.md) for the method, source priorities and per-store qualification gaps.

## Import and handoff

Each item in `config/retailer-directory-seed.json` validates as the existing v2
`RetailerDirectoryEntry`. Import the JSON array through the future #86 directory
repository. Join the sidecar by `retailer_id` to retain catalog/product/policy
entry points, evidence-linked policy findings, category unknowns and dated
technical observations. The sidecar has its own `audit_schema_version: 1`;
it does not change the shared v2 contract.

`audited` means this technical research was performed, including failed probes;
it does not mean active or qualifying. Every seed omits a live capability.
`policy_evidence_ids` stays empty because research notes are not field-bound
corpus evidence. Sidecar policy findings reference the reviewed source URLs;
#86/#87 should ingest actual source excerpts before producing those evidence IDs.
The four distributor/search leads retain unresolved identity in the sidecar and
coverage gaps; import must not silently promote them to confirmed sellers.

#51/#52 can use the route samples and failure cases for adapters. #86 consumes
the seeds, #87/#88 perform bounded collection/maintenance, and #99 validates
automatic onboarding against representative products and variants through
SourceGate. This audit is not activation evidence for those capabilities.

Offline validation:

```powershell
python scripts/check_retailer_seed.py
python -m unittest discover -s tests -v
```
