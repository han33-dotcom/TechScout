# Buyer request and product discovery repair

[Issue #194](https://github.com/han33-dotcom/TechScout/issues/194) follows the original [failed #178 evaluation](../../buyer-v1/results-2026-11-25/README.md). Its 20 submissions and four correctness scores remain frozen. The four fresh, pre-tuning questions and the additional post-pin phone question all returned zero priced or verified listings. Direct retailer search returned product links in four of those five runs.

## Browser protocol and independence

Each submission used an isolated Chrome context on the ordinary buyer UI, API and worker. The driver selected the category and entry type, typed the whole natural-language question, and submitted once. Each service cohort started with the standard Lebanese merchant directory and zero catalog offers. No seller URL, product ID, shop selection, reference answer, prefilled location or product inventory accompanied a request. The UI's 300-second observation limit was independent of the worker's 120-second ceiling. The original source registry and shared accounting limits applied. A fresh page check used a separately recorded source budget after the runs.

Development questions D1–D5 and confirmation questions C1–C5 were committed in `bb77b09` before the first product edit. D1 and D2 ask for the same black JBL and Beirut delivery. D3 and D4 differ only in RAM and SSD capacity. D6 was added as a development check before the first confirmation submission. The ten development attempts comprise six distinct requests. D4f repeated the successful development example on the final pin after the fresh checks.

The first confirmation build `6400c60` ran C1. An offline integration test then identified an opaque listing URL regression and confirmation stopped. The other four C prompts had not been submitted. After the correction, the product was pinned at `37adc26065dd7d6ca35727302950c7fb8f6fc13d`. C1 on that revision is a replay. C2–C5 are the fresh, pre-tuning confirmation group. C6 was separately reserved before the directory correction but shares a target with Arabic case H02 in the original study. Its result is excluded from fresh confirmation. C7 was [reserved on the issue](https://github.com/han33-dotcom/TechScout/issues/194#issuecomment-7174008454) after the final product pin and before its first submission or retailer lookup. It is an additional fresh phone check. These are hand-authored English delivery requests. Pickup, Arabic input, pasted deals and cross-turn refinement were outside this live sample.

Freshness checks compared all 13 distinct questions against all 43 screened inputs, 30 original held-out requests, 13 calibration requests, 230 original trial rows, and the #176, #75 and #177 development examples. No full question recurred. The A56 product overlap is reported separately. The frozen cohort verifier still reports 43 screened, 20 frozen, five seller pairs, all five categories and both entry paths and fulfillment modes.

## What changed in the shopping path

The form sends the original wording to the write API. The persisted request retains the parsed model and suffix, explicit capacities, representable requirements and preferences, the original wording and delivery destination. The worker searches reviewed merchant routes and uses the full request again when it checks a candidate. Unknown mandatory costs, unconfirmed specifications and ambiguous product selections can prevent a recommendation. [Technical review and contract notes](../../../docs/discovery-repair.md)

D1 and D2 both searched for `JBL Tune 660NC` and retained black and Beirut. D3 and D4 retained Jounieh and used different RAM/storage queries. Technodel's short search terms on their repeats were `24GB` and `8GB`. The WooCommerce searches carried both capacities. C3 retained a USD 1700 ceiling, 32 GB RAM, 2 TB SSD and Beirut. C4 kept the 100 Hz minimum and Saida. C5 kept Wi-Fi, at least 512 GB and Tripoli. A parsed constraint or a source marked checked counts as no buyer result until an actual listing appears.

## Why searches stopped short

- The exact audio paraphrases returned no matching retailer link. For the later blue 720BT request, the ITWorks URL named black but its rechecked product title named white (USD 57.82). The Technodel link covered several colors at USD 49.95 without a selected blue option. The buyer saw no blue selection with a price and stock.
- The 32 GB / 2 TB confirmation reached a 32 GB Acer page advertising a 1 TB SSD. It did not meet the storage requirement. The 100 Hz monitor search located a model-specific Samsung page, but the adapter could not establish the selected offer. The 512 GB Wi-Fi tablet search returned no candidate.
- The S24 Ultra phone searches reached a Samsung family page. The app could not bind a priced 512 GB Ultra selection. The iPhone 16 Plus development search found an open-box listing for a new-only request and displayed no offer.
- Retailer traces distinguish a returned search with no bounded match from a timeout, network error, or a gate refusal before dispatch. D5b's Technodel lookup had zero attempts. Directory-only leads handed off for onboarding did not supply product evidence.
- For development D4b the buyer saw a USD 555 current listing for an HP Laptop 15, 8 GB RAM and 256 GB SSD. The card kept tax and shipping unresolved, gave the current online stock and linked to the retailer. The initial reader also left the structured storage fields unconfirmed even though the listing title identified them. On the pinned-build replay D4f, the card and field receipts established the memory and SSD requirements. It retained the unresolved Jounieh delivery eligibility, shipping and tax. These two displays were the same product and the same development question. [Initial display](runs/D4b.png) and [pinned-build display](runs/D4f.png).

## Independent retailer checks and next build

The independent same-URL check confirmed the HP title, 8 GB RAM, 256 GB SSD, SKU TD-20TD269, new condition, online stock and USD 555. The Acer page advertised 1 TB at USD 1,070. The Samsung monitor did advertise new, in stock, 100 Hz and USD 118.80. The worker had discarded this price because an earlier headset checkout review assumed whole dollars. This was a missed priced lead. Five additional [confirmation questions](final/requests.json) were reserved before correcting the listing-price reader. The first-round results above retain their original classifications. [Gated rechecks and response hashes](retailer-checks.json)

## Acceptance limits

Scenario 04 exercised ordinary exact-model wording and variant checks. Scenario 08 exercised a live hard ceiling and an offline target-versus-maximum regression. Scenario 09 exercised the default new condition. The sample does not establish the complete scenario 08 comparison or the three condition states in scenario 09. The original four #178 scores remain 0/10 fully verified, 0/5 useful partials, 0/5 correctly explained blockers and 0/5 two-seller comparisons.

The Python 3.11 baseline passed 1,128 tests in 371.632 seconds. Frontend tests passed 33/33 and the production build succeeded. Both database testing and the Docker image build passed on the pinned product commit.

## Counts by category and entry type

Each cell counts browser submissions with the indicated outcome. A product link from a retailer search is an intermediate step. A priced listing counts only when it appears in the buyer UI. The direct-search column excludes web directory leads and the separate Class phone operation.

| Series | Category / entry | Runs | Direct search found a product link | Candidate reached research | Priced listing shown | Verified recommendation |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Development | phone / exact model | 1 | 1 | 0 | 0 | 0 |
| Development | audio / exact model | 2 | 0 | 0 | 0 | 0 |
| Development | laptop / needs | 5 | 3 | 2 | 2 | 0 |
| Development | monitor / needs | 2 | 0 | 0 | 0 | 0 |
| Fresh pre-tuning confirmation | audio / exact model | 1 | 1 | 0 | 0 | 0 |
| Fresh pre-tuning confirmation | laptop / needs | 1 | 1 | 0 | 0 | 0 |
| Fresh pre-tuning confirmation | monitor / needs | 1 | 1 | 0 | 0 | 0 |
| Fresh pre-tuning confirmation | tablet / needs | 1 | 0 | 0 | 0 | 0 |
| Post-pin fresh phone extension | phone / exact model | 1 | 1 | 0 | 0 | 0 |
| Interrupted confirmation | phone / exact model | 1 | 1 | 0 | 0 | 0 |
| Confirmation replay | phone / exact model | 1 | 1 | 0 | 0 | 0 |
| Historical product overlap | phone / exact model | 1 | 1 | 0 | 0 | 0 |

## Browser attempts

Each record contains the original question, interpreted request, retailer terms and attempt counts, web plan, source outcomes, current listing receipts where available, spending and the final rendered text. An attempted lookup can include source-managed retries. A plan alone does not establish a dispatched search. Elapsed time runs from form submission to the final UI capture.

| Attempt | Screenshot | Product revision | Direct retailer queries | Visible outcome | Final (s) |
| --- | --- | --- | --- | --- | ---: |
| [D1](runs/D1.json) | [UI](runs/D1.png) | `1a7f9b0` | itworks, technodel `JBL Tune 660NC` | No candidate shown. | 56.821 |
| [D2](runs/D2.json) | [UI](runs/D2.png) | `1a7f9b0` | itworks, technodel `JBL Tune 660NC` | No candidate shown. | 40.559 |
| [D3](runs/D3.json) | [UI](runs/D3.png) | `1a7f9b0` | beytech, mojitech `laptop 24GB RAM 512GB` | No candidate shown. | 32.117 |
| [D4](runs/D4.json) | [UI](runs/D4.png) | `1a7f9b0` | beytech, mojitech `laptop 8GB RAM 256GB` | No candidate shown. | 32.376 |
| [D5](runs/D5.json) | [UI](runs/D5.png) | `1a7f9b0` | beytech, mojitech `monitor 180Hz` | No candidate shown. | 35.068 |
| [D3b](runs/D3b.json) | [UI](runs/D3b.png) | `1abbc4e` | technodel `24GB`, beytech, mojitech `laptop 24GB RAM 512GB SSD` | Product link found. No candidate shown. | 41.969 |
| [D4b](runs/D4b.json) | [UI](runs/D4b.png) | `1abbc4e` | technodel `8GB`, beytech, mojitech `laptop 8GB RAM 256GB SSD` | Technodel USD 555 (uncertain) | 57.718 |
| [D5b](runs/D5b.json) | [UI](runs/D5b.png) | `1abbc4e` | technodel `180Hz` (gate declined), beytech, mojitech `monitor 180Hz` | No candidate shown. | 31.021 |
| [D6f](runs/D6f.json) | [UI](runs/D6f.png) | `37adc26` | technodel, beytech, mojitech `iPhone 16 Plus`, Class `iPhone 16 Plus` | Product link found. No candidate shown. | 71.442 |
| [D4f](runs/D4f.json) | [UI](runs/D4f.png) | `37adc26` | technodel `8GB`, beytech, mojitech `laptop 8GB RAM 256GB SSD` | Technodel USD 555 (uncertain) | 70.034 |
| [C1](runs/C1.json) | [UI](runs/C1.png) | `6400c60` | technodel, beytech, mojitech `Samsung Galaxy S24 Ultra`, Class `Galaxy S24 Ultra` | Product link found. No candidate shown. | 49.520 |
| [C1f](runs/C1f.json) | [UI](runs/C1f.png) | `37adc26` | technodel, beytech, mojitech `Samsung Galaxy S24 Ultra`, Class `Galaxy S24 Ultra` | Product link found. No candidate shown. | 70.199 |
| [C2f](runs/C2f.json) | [UI](runs/C2f.png) | `37adc26` | technodel, itworks `JBL Tune 720BT` | Product link found. No candidate shown. | 64.061 |
| [C3f](runs/C3f.json) | [UI](runs/C3f.png) | `37adc26` | technodel `32GB`, beytech, mojitech `laptop 32GB RAM 2TB SSD` | Product link found. No candidate shown. | 54.329 |
| [C4f](runs/C4f.json) | [UI](runs/C4f.png) | `37adc26` | technodel `100Hz`, beytech, mojitech `monitor 100Hz office` | Product link found. No candidate shown. | 39.804 |
| [C5f](runs/C5f.json) | [UI](runs/C5f.png) | `37adc26` | technodel `512GB` | No candidate shown. | 38.336 |
| [C6f](runs/C6f.json) | [UI](runs/C6f.png) | `37adc26` | technodel, beytech, mojitech `Samsung Galaxy A56 5G` | Product link found. No candidate shown. | 55.851 |
| [C7f](runs/C7f.json) | [UI](runs/C7f.png) | `37adc26` | technodel, beytech, mojitech `Motorola Edge 60 Fusion` | Product link found. No candidate shown. | 46.901 |

## Time and accounting

- Development used USD 0.012882 settled, USD 0 uncertain and USD 0 held. 10 terminal displays took 31.021 to 71.442 seconds.
- Fresh pre-tuning confirmation used USD 0.003794 settled, USD 0 uncertain and USD 0 held. 4 terminal displays took 38.336 to 64.061 seconds.
- Post-pin fresh phone extension used USD 0.000964 settled, USD 0 uncertain and USD 0 held. 1 terminal displays took 46.901 to 46.901 seconds.
- Interrupted confirmation used USD 0.001182 settled, USD 0 uncertain and USD 0 held. 1 terminal displays took 49.520 to 49.520 seconds.
- Confirmation replay used USD 0.001384 settled, USD 0 uncertain and USD 0 held. 1 terminal displays took 70.199 to 70.199 seconds.
- Historical product overlap used USD 0.00099 settled, USD 0 uncertain and USD 0 held. 1 terminal displays took 55.851 to 55.851 seconds.
