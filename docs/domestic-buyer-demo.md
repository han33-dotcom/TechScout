# Domestic buyer demo

The default worker runs the domestic buyer controller. Start the local application and its buyer worker using the repository setup, then open the shopping form in a fresh session. For an exact comparison, enter **Soundcore H30i**, select audio and delivery to Beirut. For a needs-based comparison, select a category and describe the required features. A product link limits the search to its selected seller and variant.

The operator can use `python -m app.maintenance status` to inspect the last bounded refresh before opening the form. Instructions for seeding, running the worker and refreshing the corpus are in the [setup guide](../README.md) and [maintenance report](catalog-refresh.md).

## Live delivery request

At 21:07:51 UTC on November 23, 2026, a new API session submitted the Soundcore request against the domestic corpus after the bounded background run. The normal worker ran in a separate process. It looked up two stored candidates with the same Anker configuration identifier, skipped web search and rechecked the source pages. A current collection-page receipt tied the WinCell variant to Lebanese dispatch. OutGeeked's listing established its own dispatch origin.

| Seller | Black H30i item price | Dispatch | Payable total to Beirut |
| --- | ---: | --- | --- |
| OutGeeked | USD 20 | Lebanon | Unknown |
| WinCell | USD 40 | Lebanon | Unknown |

Both source pages reported stock. Neither established the complete combination of new condition, purchasability, destination eligibility and mandatory charges. The buyer saw two uncertain alternatives and zero primary recommendations. The worker took 9.235 seconds and settled USD 0.001012 with no held or uncertain spend. The ledger recorded five retrievals and one model call for run `4c21fd51-cad5-4457-8229-44e09a3f2bc6`. The earlier browser iPhone demo also abstained. The outstanding live qualifying browser result is tracked in [#75](https://github.com/han33-dotcom/TechScout/issues/75).

## Coverage and repeatable scenarios

| Category | Verified with synthetic exact and needs requests | Measured local-source coverage |
| --- | --- | --- |
| Phones | Storage, configuration and two-seller delivery. A separate link request verified pickup at one named branch. | Class Cellulars phone reads and Beytech catalog names. No live qualifying result in the recorded iPhone browser run. |
| Laptops | RAM, storage, budget, competing sellers, new-shop admission and one evaluation follow-up. | The background laptop gap queued three leads. No new usable laptop seller was demonstrated. |
| Tablets | A fresh session selected a model with a documented storage value. | No measured same-variant domestic pair. |
| Monitors | A fresh session used the selected model's published refresh rate. | No measured same-variant domestic pair. |
| Headphones and earbuds | The selected item's headphone form factor supported a needs comparison. | The two H30i observations above. Three additional Beytech headphone listings yielded a current price during maintenance. |

Unlisted retailer/category pairs have no demonstrated coverage in this exercise. The measured seed corpus includes catalog names without exact offers. Marketplace identity, branch inventory, taxes, destination and fee evidence still limit individual sellers. The detailed [seed coverage](domestic-seller-coverage-2026-11-23.json) and [source coverage](source-coverage.md) retain these distinctions.

From the repository root, run the offline scenarios with Python 3.11.

```sh
python -m unittest discover -s tests -p test_domestic_flow_matrix.py -v
python -m unittest discover -s tests -p test_domestic_buyer*.py -v
python -m unittest discover -s tests -p test_onboarding.py -v
python -m unittest discover -s tests -p test_maintenance.py -v
```

The matrix submits both entry paths for every category to the owned API. Historical USD 120 prices become USD 99 and USD 100 on the final source reads. The verified delivery totals are USD 104 and USD 105. A cold corpus also obtains two locally dispatched listings. The pickup scenario requires a pickup-only Offer for exactly one named domestic location and branch-specific current stock. A quoted shipping range stays an estimate, and an unknown tax leaves the total incomplete.

The buyer scenarios exercise three independent researchers, the 120-second active deadline with a 30-second final reserve, a single evaluator follow-up, and at most three recommendation groups. The one-seller case queues an unknown shop while verification continues. The outage case still verifies the directly readable finalist. The onboarding scenarios run successful activation and failed validation with a recorded reason. The newly admitted shop supplies a fresh synthetic USD 910.25 finalist without an edit to the registry file. The maintenance scenarios cover arrivals, price changes, source failure and retained observation times.
