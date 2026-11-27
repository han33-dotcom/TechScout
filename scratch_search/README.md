# Scratch offer-search experiment

This CLI researches new iPhone 17 Pro item prices at Lebanese shops. The integration branch is `scratch`. Create experiment branches from `scratch` and target it with their pull requests. The first checkpoint measures a single search. [Tracking issue #256](https://github.com/han33-dotcom/TechScout/issues/256)

## November 27 development checkpoint

On the synthetic market, the final fixed and adaptive runs each returned both expected capacity winners with zero unsupported winners and reproducible evidence. Earlier development runs missed the 512 GB offer after a selector change. The worker now waits for a page update and can cite exact numbered lines and correct a rejected claim while the evidence is still on screen.

The live adaptive run called OpenRouter 11 times and took 44.172 seconds at a reported cost of USD 0.0227412. It retained two Class Cellulars variation rows and six Beytech rows. The Class rows were out of stock. The Beytech rows had prices and stock, but new condition remained unverified. Mojitech returned an unusable catalog response. The comparison produced zero qualifying live offers. There is no independently audited live reference for this smoke test. [Machine-readable receipts and synthetic scores](checkpoint.json)

## Run a search

From the repository root, use Python 3.11 and the project virtual environment. Install the shared requirements and the experiment browser.

```sh
python -m pip install -r requirements.txt -r scratch_search/requirements.txt
python -m playwright install --only-shell chromium
```

Set `OPENROUTER_API_KEY` in the environment or the Git-ignored root `.env`. Both strategies call OpenRouter, including when the retailer pages are synthetic. The default model is `openai/gpt-4.1-mini`. Use `--model` to select another OpenRouter model with JSON output.

```sh
python -m scratch_search "Find the best new iPhone 17 Pro offers across storage sizes" --market synthetic --strategy fixed
python -m scratch_search "Find the best new iPhone 17 Pro offers across storage sizes" --market synthetic --strategy adaptive
python -m scratch_search "Find the best new iPhone 17 Pro offers across storage sizes" --market live --strategy adaptive
```

Every invocation writes a new directory under `var/scratch-search`. Its `report.json` contains the buyer answer, parsed brief, catalog outcomes, excluded titles, worker assignments, page actions, accepted observations, validation failures, uninspected candidates, timing, model calls, token counts and provider-reported cost. A missing cost stays unknown. Each SHA-256-named capture contains the page URL, UTC time, rendered HTML, visible text and control state. The typed worker observations carry the corresponding capture hash and exact quotations. Interpret model notes as hypotheses. The comparison uses accepted observations.

## What runs

The experiment searches public catalogs at Class Cellulars, Mojitech and Beytech through the repository's reviewed source transport and workload limits. A catalog result identifies a candidate. Workers inspect its public product document. Chromium renders the fetched document in an isolated context, runs inline scripts and allows interaction with its controls. Separately loaded scripts and background retailer requests are blocked in this checkpoint. For pages that publish WooCommerce variation rows in the document, the report retains those rows and links them to the captured HTML. A row alone cannot qualify an offer as new or establish ordinary purchase terms.

The fixed strategy visits candidate listings in round-robin retailer order. The adaptive planner receives the same candidates and completed worker results and chooses batches of up to two. Both strategies have the same limits of six listing inspections, seven browser decisions per listing, and 24 model calls per run. Prices use decimal arithmetic. Confirmed offers are ranked per capacity with ties retained. The CLI supports a hard USD ceiling and a requested capacity or color. It reports search failures and keeps observations that are missing a required fact available for inspection.

## Evaluate a result

The offline tests drive real Chromium over synthetic documents with scripted model decisions. They exercise the ordinary entry point, including option changes, delayed price updates, evidence validation, conditional pricing, missing stock, wrong-model rejection, ties and the scoring path.

```sh
python -m unittest discover -s tests -p test_scratch_search.py -v
python -m scratch_search.score var/scratch-search/RUN_ID/report.json scratch_search/fixtures/reference.json
```

The bundled reference applies to the exact synthetic request in the commands above. For a live evaluation, supply a separately audited reference for the same request and time window. Include all audited shops, even those the run missed, and all qualifying seller listings with capacity, color and item price. If the page changed and the result cannot be independently resolved, keep that case outside the scored denominator and record why.

Read correctness first (unsupported recommendations and missing or changed evidence), then coverage (winning capacities and missing ties), then practicality (elapsed time and reported cost). A single development request exercises the mechanics. The blinded multi-shop study, discovery of new retailers, follow-up requests and durability across process restarts remain later milestones.
