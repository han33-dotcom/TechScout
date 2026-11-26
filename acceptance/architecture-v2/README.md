# Iteration 2 comparison freeze

[Manifest v1](manifest-v1.json) pairs the merged #197 control with the single-agent finalist that #200 will build. The existing 20 cases decide product acceptance. Six requests committed before retailer lookup provide a separate generalization check across the five categories. The independent checks establish at least one complete delivered offer. Other checks include an unavailable finish, uncertain prices or charges, and a pickup request without verified branch stock.

Both arms start each case with 23 retailer directory records and an empty offer catalog. Both can search reviewed retailer catalogs, read listing and policy pages, and use the configured manufacturer directory. The freeze host had no configured general web-search connector. This comparison measures architecture with that tool set. A broader search configuration would be a separate experiment.

## Keep the evaluation independent

During #200 and the #201 smoke test, use separate development requests. Keep both scored cohorts, reference URLs, grader files and captured answers out of prompts, tools, fixtures, indexes and debugging. The two `*-inputs.json` files contain the only buyer fields the evaluation driver may submit. Use the normal UI and default new condition. Leave an unspecified branch blank. Supply no invented address or clarification.

Before scoring, record the finalist commit and served-asset hashes, effective tool and model configuration, catalog digest and evidence-bank digest. Capture evidence independently for replay. Both arms receive the same response for the same scoped request and the same explicit miss for an uncaptured request. The agent decides which tools to call. Keep release changes to seller eligibility or accounts out of a one-sided comparison.

## Read the scorecard

Score the original 20 against the [buyer protocol](../../docs/buyer-correctness-acceptance.md). Score the six fresh requests separately, showing each buyer-visible result against its dated evidence. An out-of-stock listing establishes a seller-specific blocker. A search that finds no exact listing leaves availability unresolved. A newly found qualifying offer can earn credit after independent verification. Record reference drift for both arms and retain the original evidence.

Measure both timers from submission. Report an unreached first useful display explicitly. For cost per useful answer, divide settled spend on all originals by the number of distinct useful answers and report uncertain spend alongside it. Replay cost and timing describe replay conditions. Use live runs for buyer-facing cost and latency.

## Verify offline

From the freeze checkout, run `python acceptance/buyer-v1/verify_freeze.py` and `python acceptance/architecture-v2/verify.py`. To recreate the starting catalog, run the manifest’s two build commands on a new SQLite database and pass its path to the second verifier with `--catalog PATH`. This checks the logical directory digest and empty offer, evidence and session state. No model or retailer call is needed.
