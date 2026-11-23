# Refresh a domestic catalog batch

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
