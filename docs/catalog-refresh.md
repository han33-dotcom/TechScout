# Refresh a domestic catalog batch

## Run the local maintenance process

Point the API, buyer worker and maintenance process at the same migrated application database and evidence directory. All processes use the same provisioned project ledger. The schedule starts paused. Use the following commands from the repository root.

```sh
python -m app.maintenance enable
python -m app.maintenance serve
```

In another terminal, use `status` to see due times, pending gaps, prior results and owned work. Use `pause` to stop dispatch, including new attempts in an active job. A sent request can still settle. `tick` polls once. `trigger discovery`, `trigger catalog` and `trigger offers` each run one enabled job. `gap laptop` increments the same bounded category counter used by the buyer worker. Runtime demand stores only categories and digests of URLs from the catalog. It expires high-use priority after seven days.

Use `--settings FILE` and `--manifest FILE` before the command to select the operating bounds and reviewed seed set. Keep these stable across processes sharing a schedule. The defaults in `config/maintenance.json` run discovery on startup and daily, scan catalogs every six hours, and check the offer due list hourly. A gap can trigger an earlier discovery after a one-hour cooldown. Each objective uses the configured backend OpenRouter role to inspect catalog coverage, choose a channel and write or revise a public search query. The five-channel rotation uses configured connectors. The fallback for a completely unconfigured rotation slice is another configured connector. Leads enter the regular admission queue. The existing onboarding worker validates capabilities independently.

Catalog results can add individual product URLs to the next offer batch only with a permitted first-party HTML product read, matching category, SKU and product identity. Reports distinguish enumeration from extraction. An exact, seller-bound discontinued offer records a variant retirement with its original page receipt. A partial catalog, unavailable page or missing price is insufficient. Pending embeddings share the job's limits. An indexing failure leaves structured observations intact. Each report gives before/after counts of recently priced domestic-dispatch sellers and same-variant seller pairs by category. Only a buyer run can verify a finalist.

Each job has its own durable lease, checkpoint, UTC accounting day and page, call, time and spend ceilings. A recovered discovery with an unacknowledged dispatch stops conservatively. Recoverable refresh batches retain each attempted page. The ledger also coordinates in-flight exact URLs across buyer and maintenance processes and limits maintenance across the campaign to USD 42 over fourteen used days.

### Bounded live run on 23 November 2026

The operator enabled a single run on a copy of the seeded November 23 corpus, submitted one laptop gap, triggered each job in a separate process and paused the schedule. The discovery used one OpenRouter call and one gated read of the ASUS Lebanon retailer directory. Of five distinct leads, three entered validation, one was already configured and one was excluded. The four API search connectors were unconfigured on this machine.

| Job and UTC start | External calls | Observed result | Settled USD |
| --- | ---: | --- | ---: |
| Discovery, 20:56:08 | 2 | Three new retailer leads | 0.000884 |
| Catalog, 20:56:16 | 2 | One Beytech catalog page, ten headphone product URLs, one embedding call | 0.00000224 |
| Offers, 20:56:26 | 5 | Three newly enumerated listings yielded exact prices, two embedding calls, three due pages outstanding | 0.00000074 |

Total settled spend was USD 0.00088698 with zero unresolved reservations. The offer job reached its three-fetch limit. One listing's pending embeddings exceeded the manifest allowance. New price observations covered the Beytech ACE Black, Sony MDR-G300 and Sony WH-1000XM5 listings. The five categories gained zero recent domestically dispatched sellers and zero same-variant pairs during these jobs. The live result demonstrates acquisition and admission handoff. The offline schedule scenarios exercise price and specification changes, removal, backoff, concurrent buyer reads, and worker interruption.

The accounting job identifiers are `discovery:discovery-155b9fffc6574037a98dce6637c57858`, `refresh:catalog-9f303ada88e044feb6bd6b4ed54b8d47` and `refresh:offers-1621ac8af7d3485eab6ea8d89b1d93d6`.

## Trigger a reviewed manifest directly

Use the same migrated application database, evidence directory, source registry and retailer directory as the seeded catalog. The planner reads a reviewed seed manifest. Only an explicit run ID starts collection. The command runs once and exits.

```sh
python -m app.refresh config/domestic-seller-coverage.json --limit 2
python -m app.refresh config/domestic-seller-coverage.json --limit 2 --run-id domestic-refresh-001
```

Choose a new ID for a new batch. Repeat the exact second command to resume or inspect the original selection. A changed manifest, limit or high-use selection requires a new ID. Every process resuming a batch must use the same application database and evidence directory. The run also needs the [shared maintenance ledger](shared-accounting.md).

## Reading the plan

The plan shows the last check, next scheduled check, reason for eligibility, selected URLs and remaining due count. New resources come first, followed by the earliest due resources. Use a repeated `--high-use MANIFEST_URL` option for known high-use offers. Use the same options when planning and executing. The stable URL-based rotation spreads high-use offer checks over 12–24 hours and other offer checks over 3–7 days. Catalog pages use six hours, general policies seven days and specifications thirty days. A changed processing recipe becomes eligible immediately. A failed source read backs off for six hours. Directory pauses, source permissions and documented retirements remain visible.

## Reading the result

Page outcomes distinguish new resources, changed fields or passages, unchanged content, processing changes, failures, skipped work and superseded responses. An HTTP 304 or a banner-only change preserves the underlying fact observation date. A 404 or a timeout records a failed check. A missing item in a partial catalog provides no proof of removal. The buyer worker still checks finalist price and stock during the buyer run.

Requests use the shared source gate. The collector sends validators only to the matching final URL and uses a full read when the snapshot, validators or processing version preclude reuse. The batch reports extractor invocations, changed field paths, changed passage count, source attempts, settled spend and unresolved reservations. This command runs structured and keyword ingestion. Changed passages remain available to the existing embedding indexer.

The manifest bounds fetch attempts, response bytes, records, elapsed time and retries. A batch uses the project's shared UTC-day maintenance allowance. Up to fourteen used days at up to USD 3 per day fit inside the USD 500 campaign. A lease excludes a second owner of the same batch. If a worker disappears during a read, recovery reports that page as interrupted, retains its conservative allowance and continues with other pending pages. A later due batch can revisit the page. An incomplete batch exits with status 1.

## Live check on 23 November 2026

The operator used a copy of the November 23 domestic corpus and the operational shared ledger. The manifest selected the headphone and iPhone catalog queries from `config/domestic-bootstrap-validation.json`, capped at two GET attempts, zero retries, 20 records, 4 MiB, 90 seconds and zero paid calls. The ledger job was `refresh:beytech-domestic-20261123-01`.

| Beytech catalog | UTC check | HTTP | Catalog rows | Outcome |
| --- | --- | --- | ---: | --- |
| Headphones | 20:14:54 | 200 | 10 | New page, ten product-name passages |
| iPhone | 20:14:56 | 200 | 10 | Existing page reprocessed under the manifest recipe, three affected passages |

Both GETs settled at USD 0 with zero unresolved reservations. A second process ran the identical command and produced a byte-identical JSON report. The shared ledger still held exactly two completed source attempts. This sample measured catalog acquisition. It did not measure verified offers, seller discovery, scheduled operation or buyer savings.
