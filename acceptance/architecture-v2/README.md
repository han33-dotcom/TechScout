# Iteration 2 comparison freeze

[Manifest v2](manifest-v2.json) pairs the #197 decision baseline with the single-agent finalist that #200 will build. Both use the same search and browser tools. The original 20 cases decide product acceptance. Six reserved requests check generalization across the five categories. [Original freeze](manifest-v1.json)

Both arms start each case with 23 retailer records and an empty offer catalog. They can use SerpApi, Brave, Firecrawl Search and the manufacturer directory. Enable Tavily for both if its access check passes. Web search can find new sellers. The shared source review determines which retailer operations can run.

For a known listing, either arm can use direct reads, Firecrawl Scrape or the same interactive Chromium browser. The browser can search a shop, select a variant or pickup branch, and request a delivery quote up to the point of purchase. If the seller needs an address the case does not supply, preserve the missing fee.

[#201](https://github.com/han33-dotcom/TechScout/issues/201) owns shared integration and the development smoke test. Demonstrate a web search and an interactive selection or quote through each implementation. Pin both runnable revisions, the common adapter, effective providers and browser build before scoring. Capture browser state transitions for replay. The offline verifier checks the protocol and case integrity; runtime access needs separate receipts.

The [paired development runner and receipts](../development/paired-shopping/README.md) record effective provider access, retailer selections and the buyer-visible outcomes for both pinned revisions.

## Keep the evaluation independent

During #200 and the #201 smoke test, use separate development requests. Keep both scored cohorts, reference URLs, grader files and captured answers out of prompts, tools, fixtures, indexes and debugging. The two `*-inputs.json` files contain the only buyer fields the evaluation driver may submit. Use the normal UI and default new condition. Leave an unspecified branch blank. Supply no invented address or clarification.

Before scoring, record the finalist commit and served-asset hashes, effective tool and model configuration, catalog digest and evidence-bank digest. Capture evidence independently for replay. Both arms receive the same response for the same scoped request and the same explicit miss for an uncaptured request. The agent decides which tools to call. Keep release changes to seller eligibility or accounts out of a one-sided comparison.

## Read the scorecard

Score the original 20 against the [buyer protocol](../../docs/buyer-correctness-acceptance.md). Score the six fresh requests separately, showing each buyer-visible result against its dated evidence. An out-of-stock listing establishes a seller-specific blocker. A search that finds no exact listing leaves availability unresolved. A newly found qualifying offer can earn credit after independent verification. Record reference drift for both arms and retain the original evidence.

Measure both timers from submission. Report an unreached first useful display explicitly. For cost per useful answer, divide settled spend on all originals by the number of distinct useful answers and report uncertain spend alongside it. Replay cost and timing describe replay conditions. Use live runs for buyer-facing cost and latency.

## Verify offline

From the freeze checkout, run `python acceptance/buyer-v1/verify_freeze.py` and `python acceptance/architecture-v2/verify.py`. To recreate the starting catalog, run the manifest’s two build commands **at the pinned control revision**, with `DATABASE_URL` pointing to a new SQLite file. Pass its path to the freeze checkout’s verifier with `--catalog PATH`. This checks the logical directory digest and empty offer, evidence and session state. No model or retailer call is needed.
