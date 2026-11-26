# Paired development runs for #201

The runner builds the two frozen applications from Git archives and submits development requests through their own buyer forms. The control uses its existing discovery planner and listing reader. An unresolved listing can invoke the common browser through the reader. The agent chooses inspections and selections through its existing action menu. Both continue through their pinned evidence checks and publishers. [Iteration 2 freeze](../../architecture-v2/README.md) · [Development results](receipts/README.md)

| Component | Revision |
| --- | --- |
| Control | `34918b296c1615cba2741eb6962d5b8ba185ddad` |
| Single agent | `c9c71033d7daf5508683e216f165d77c3eaab026` |
| Shared adapter used for fresh verification | `625afd2e18144416a93fb0442f1cb6c3366948cb` |

## Reproduce

Use Python 3.11 with the repository requirements, Node and npm. Install the development-only browser dependency and Chromium. Run these commands from the repository root with the virtual environment active.

```text
python -m pip install -r acceptance/development/paired-shopping/requirements.txt
python -m playwright install chromium
python -m unittest discover -s acceptance/development/paired-shopping/tests -v
```

The live steps use the configured shared PostgreSQL accounting endpoint, the `techscout-december-2026` campaign and the backend OpenRouter key. The current repository's ignored `.env` is the default provider file. Pass `--env-file PATH` when using a different backend file. Both arms need the same configured credentials and approved unit rates. [Provider settings](../../../docs/discovery.md)

```text
python acceptance/development/paired-shopping/pair.py prepare --work var/paired-dev
python acceptance/development/paired-shopping/pair.py run --work var/paired-dev --case setup --label setup
python acceptance/development/paired-shopping/pair.py run --work var/paired-dev --case search --label discovery
python acceptance/development/paired-shopping/pair.py run --work var/paired-dev --case selection --label selection
```

Preparation requires a new working directory. It builds both UIs, migrates and imports the catalog at the control revision, then verifies the logical directory digest and all required empty tables. Each provider probe uses its own copy of that seed. The probes exercise the configured discovery channels on a separate ASUS Zenbook request and attempt the reviewed Technodel FAQ through configured managed scrape. Tavily is enabled only when both probes succeed. A failed required probe leaves the profile ineligible for scoring. The runner can still exercise available tools on development inputs.

Each submission gets a new product database, evidence directory, browser context and buyer session. The preparer snapshots the adapter, and both servers load that copy through the pinned `app.buyer_flow:run` worker entry point. A local submission clock spaces buyer submissions by at least 60 seconds across invocations in the same checkout. Both workers use the manifest's low-effort OpenRouter route, 300-second active and browser allowances, 120-second ledger deadline, 40 external calls and USD 1.00 cap. Research preserves the 30-second, ten-call and USD 0.25 final reserve. Source concurrency, rate and per-run limits apply at the original source gate. The control's active allowance is injected at the service boundary because its constructor predates the 300-second setting.

The setup UI request is an Honor X9d for delivery to Tripoli. Galaxy A17 requests to Jounieh exercised the adapter while it was being developed. Fresh verification uses a Galaxy A07s exact-model request and a separate Class Cellulars product link for delivery to Batroun. The link lets the control exercise its normal retailer-read path even when general discovery is offline. A directory lookup, onsite search or pasted URL cannot satisfy the general-search demonstration. Output labels must be new.

## What the shared browser observes

The adapter accepts reviewed product URLs for the selected category and retailer. SourceGate fetches the actual document under the job budget. Playwright renders those bytes at the exact URL. An isolated Chromium context blocks subresources, scripts, sockets and form submissions. The browser uses the retailer's native WooCommerce controls and reads back their selected values. A complete attribute tuple must identify one row in the retailer's embedded variation data. The receipt records the previous and selected values, product and variation IDs, document hash, source time and action time.

For Class Cellulars phones, the selected product and variation are checked against the reviewed Store API slug endpoint and the existing WooCommerce price and stock reader. An exact catalog name and matching product-page heading can identify a phone outside the reader's fixed model dictionary. Delivery eligibility, dispatch origin and applicable fees need their own evidence. The pinned selector, verifier and UI determine what the buyer ultimately sees.

Both arms have the original bounded onsite catalog search plus the shared browser's open, refresh and native variant-selection operations. The pinned source scopes include no reviewed checkout or quote submission. JS-only selectors, interactive branch inventory, remote quote forms and address submission have no demonstrated support in this profile.

## Shared replay

Capture a bank independently of either buyer, then give the same directory to both arms.

```text
python acceptance/development/paired-shopping/replay/capture.py --work var/paired-dev --output var/paired-bank
python acceptance/development/paired-shopping/replay/check_tools.py --work var/paired-dev --bank var/paired-bank/bank
python acceptance/development/paired-shopping/pair.py run --work var/paired-dev --case selection --mode replay --bank var/paired-bank/bank --label replay-selection
python acceptance/development/paired-shopping/pair.py run --work var/paired-dev --case search --mode replay --bank var/paired-bank/bank --label replay-search
```

The independent collector issues the offered development search queries and exercises a bounded path through the retailer's published variants. Search keys include provider, query, country, language, result limit and cursor. Browser keys include seller, listing URL, prior state, action, arguments, quantity, branch and a development destination reference. The response bank retains its first response per key. Live callers see their actual response even if a later observation differs. Replay misses are explicit and do not fall through to live retailer or search traffic.

Replay retains the capture timestamps in the bank and timestamps response delivery to the isolated replay run for the pinned freshness checks. The model still makes live, metered decisions. Replayed data calls consume logical source or provider slots under the active job. Previously recorded data-provider charges are historical. Replay timings and settled spend describe replay conditions.

Each arm writes its buyer text and screenshot, controller trace, search and browser events, and a run lock with served-asset, model, source-policy, seed, adapter and bank hashes. Raw banks contain full retailer HTML and stay in the ignored working directory. Public receipts contain buyer-visible output and selected field evidence. The ordinary test suite uses offline fixtures.

The optional `check_tools.py` probe starts each pinned import in a separate process and compares the successful captured directory search and the first color transition directly. Its disposable fixture ledger and disabled live transport make this an offline check of tool equivalence. The paired UI replays above retain the buyers' independent decisions and operational model accounting.
