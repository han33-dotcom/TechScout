# Selected-SKU identification and category-aware discovery

The two demonstrated #196 losses were recovered on development replay. Technodel's black Logitech H540 appeared at USD 36, and the tablet buyer saw a different qualifying Wi-Fi 256 GB iPad at USD 562. Neither had verified delivered charges in the buyer UI.


| Cohort | Correct page found | Hard match confirmed in UI | Current price shown | Verified delivered total |
| --- | ---: | ---: | ---: | ---: |
| Five fresh first submissions | 3/5 | 1/5 | 2/5 | 0/5 |
| Exact variants within those five | 1/3 | 0/3 | 0/3 | 0/3 |
| Needs within those five | 2/2 | 1/2 | 2/2 | 0/2 |
| Separate development replays | 3/3 | 3/3 | 3/3 | 1/3 |

Of the three cases with a frozen new, purchasable reference, the search located a qualifying page on 3/3, showed a price on 2/3, and confirmed the hardware in the UI on 1/3. For needs requests, a different qualifying model can count. No fresh case was tuned or resubmitted.

## Method

Five previously unused prompts across four categories were committed at `22d8baa` before lookup. The [independent references](references.json) were committed at `ea98c5d` before product edits. All five first submissions count. Each used an empty Chrome session and the normal buyer form, with only entry mode, category and the original buyer wording. The two series used separate freshly migrated databases, 23 directory records and zero starting catalog offers. No reference URL, seller or quote was supplied to the application. The older #178 frozen cohort was neither changed nor rerun.

[Build and limits](build.json) record the pinned product commit, served frontend hashes, reviewed registry and inference config. The worker used a 300-second development lease with the ordinary shared 120-second accounting allowance. A minute separated successive browser submissions. The capture for each case contains its submitted form, interpreted request, bounded retailer queries, stage timings, offer receipts, charges, accounting snapshot, visible text, links and screenshot.

A correct page means that the worker located a listing whose selected product meets the hardware request, corroborated by the independent page check. The buyer receives hard-match credit only when the card confirms the exact variant or all requested hardware constraints. A current price requires a visible retailer link and current-run price evidence. Delivered-total credit requires a verified payable offer to the requested city. Each count is per request. Intended use is reported separately when the application treats it as an extra requirement.

## Independent merchant checks

| Case | New purchasable match before tuning | Retailer evidence for delivery |
| --- | --- | --- |
| S1, black JBL | Unconfirmed. ITWorks' black URL selected a white pair. A Lebtech Store lead was rate-limited on direct reread. | No qualifying quote. A post-submission IT Promax check established new stock at USD 45 but did not establish the finish. |
| S2, 128 GB Galaxy A16 4G | iGoteck offered the requested hardware in stock, with SKU 8044. The selected page did not establish new condition. | USD 199 with tax included and nationwide delivery free above USD 100. The total is conditional on accepting the unverified condition. |
| S3, 1 TB tablet | Technodel had a new 13-inch iPad Pro M5 Wi-Fi 1 TB at USD 1,733. | Free shipping was corroborated. The selected price's tax treatment remained unconfirmed. |
| S4, 200 Hz monitor | ITWorks had a new 200 Hz MSI MAG 274QPF E20, selected variant 52715377492199. | **USD 397.94 to Tripoli** for one unit, comprising USD 392.94 including tax and USD 5 nationwide delivery. |
| S5, Logitech H110 stereo headset | Technodel had new H110 SKU AK-7RGCJT3V in stock at USD 9. | The item and policy agreed on USD 4 shipping to Byblos. Tax treatment remained unconfirmed. |

Three of five had an independently established purchasable match in new condition. One of those three had all mandatory charges established. These are lower bounds for the checked merchants. The iGoteck phone's monetary facts are recorded separately so its condition gap cannot be mistaken for a missing charge.

## Product changes and development evidence

- The selected Technodel purchase component and structured Offer must agree on SKU, URL, name, stock, price and merchant. An explicit model can identify the device behind a longer title. A bounded title layout can also supply the model when category, brand and finish agree. Structured conflicts, wrong suffixes and unselected variants remain excluded. The checkout rule is unchanged for these newly recognized titles.
- Needs searches now send the category and distinguishing specifications to Technodel and the applicable WooCommerce shops. Search cards rank leads. A selected product supplies the evidence. Existing caps, source admission, phase deadlines and final checks apply to every route.
- [H540 replay](runs/D6.json) · [Screenshot](runs/D6.png). The original run reached SKU RK-95T7S1VA and discarded it. The replay showed new, black, wired, in stock, USD 36. Delivery eligibility, shipping and tax remained unresolved.
- [Tablet replay](runs/D4.json) · [Screenshot](runs/D4.png). The former capacity-only miss became a three-merchant search for `tablet 256GB wifi`. Technodel returned a new Wi-Fi iPad A16 with 256 GB, SKU AZ-ZBSPJCXK at USD 562. Beytech returned no matching link and Mojitech timed out. The buyer could see the correct variant and price, with incomplete charges.
- The offline controls vary the finish, model suffix and exact capacity on otherwise identical products. A synthetic tablet with a merchandising title, structured model and selected 64 GB capacity was read at a second retailer after the first returned no tablet. Both actual search endpoints were exercised through the offline source gate.

- [Second-merchant replay](runs/D1.json) · [Screenshot](runs/D1.png). The prior black H151 request again showed two sellers. ITWorks Lebanon qualified at USD 33 delivered to Saida (USD 28 with tax included plus USD 5 shipping). The Technodel listing was USD 16 with incomplete charges. This is the only verified delivered total in the eight runs, and it belongs to development.

Applicable buyer scenarios include 04, 09 and 17. The live cohort contains no hard-versus-approximate budget comparison for scenario 08, and does not demonstrate a complete showroom conversation.

## Fresh first submissions

| Buyer | Correct page found | Variant or hard specs confirmed in UI | Current price shown | Delivered total | First observed loss |
| --- | --- | --- | --- | --- | --- |
| [S1, black JBL Tune 510BT to Saida](runs/S1.json) | No | No | None | No | Technodel returned no matching link. ITWorks returned a black URL whose current purchase form selected white. The color exclusion was correct. |
| [S2, 128 GB Galaxy A16 4G to Jounieh](runs/S2.json) | No | No | None | No | The form preserved 128 GB and 4G but treated the closing price question as a hard constraint. Technodel returned an A16 page and the selected-item reader could not qualify it. The independent reread showed zero stock and no selected storage field for SKU DR-WM1QE62A. |
| [S3, 1 TB tablet to Beirut](runs/S3.json) | Yes | Yes | USD 1,701 | No | The 11-inch iPad Pro M5 card confirmed 1,024 GB and Wi-Fi on SKU AZ-M2CRZ509. Interpretation also turned the film-library purpose into an unresolved constraint. The current comparison did not establish delivery eligibility, tax or shipping. |
| [S4, 200 Hz monitor to Tripoli](runs/S4.json) | Yes | No | USD 180 | No | The 200 Hz Lenovo Legion title and price reached the UI. The SKU lacked a confirmed structured refresh field, and the gaming purpose was marked unresolved. ITWorks' independently quotable MSI was outside its audio-only buyer route. |
| [S5, Logitech H110 stereo headset to Byblos](runs/S5.json) | Yes | No | None | No | The worker read the in-stock H110 reference URL. The request retained “stereo” in the model, and the selected SKU had a long descriptive title with no separate model field. The combination yielded zero candidates before final verification. |

[All eight browser captures and machine-readable traces](runs/) · [Post-run product checks](page-rechecks.json) · [Additional independent reads and unsuccessful attempts](supplemental-checks.json) · [Stage scoring](outcomes.json)

The two priced fresh cards both came from Technodel. Neither established a delivered total, and no fresh buyer received a comparison between two verified sellers. The frozen reference cohort had one fully quotable new offer. The buyer recovered **0/1** of those totals. The USD 199 phone quote depended on unresolved condition. For the independently matched tablet and headset, shipping was corroborated but item-level tax remained unresolved. Research gaps in the frozen checks do not establish that a model was unavailable elsewhere.


## Timing and validation

All five fresh requests finished within 120 seconds (42.7–65.5 seconds). The first linked price appeared after 65.5 seconds for S3 and 46.2 seconds for S4. Three buyers saw no linked price. All three replays finished within 120 seconds (51.8–98.8 seconds). Reported settled spend was USD 0.006324 for the five first submissions and USD 0.005824 for the three replays. All eight snapshots reported zero held and zero uncertain spend.

The product pin's first full suite exposed a test setup error: the new fallback assertion expected a retailer absent from the fixture directory. The final test imports the audited directory before exercising fallback. An additional synthetic tablet regression establishes that the next merchant can supply a selected SKU. Both are test-only changes. The final Python 3.11 SQLite suite passed 1,137 tests in 415.148 seconds, all 34 frontend tests passed, and the production frontend built. Run `python acceptance/sku-discovery/verify.py` for the eight new captures. The archived #195 and #196 validators still recognize 24 and 16 submissions respectively. The #178 validator still recognizes 43 screened requests, 20 frozen and five reference comparison pairs.
