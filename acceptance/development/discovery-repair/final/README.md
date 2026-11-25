# Final pinned browser results

The five previously unsubmitted requests reached terminal buyer displays on product commit `cb2933886cf68b534dae738c8980a5853143ebe1`. Direct retailer searches located a product link in 4/5. Buyers saw a priced listing in 3/5 and a verified recommendation in 0/5. All four distinct buyer-visible URLs across these six runs returned HTTP 200 and corroborated the displayed SKU, listed price and online stock. A sixth submission replayed the monitor request that exposed the fractional-price issue and is counted as development.

All five final questions were committed in `fad4ca9` before the pricing change, with no retailer lookup or submission for these inputs. The two exact-model and three needs-based questions span all five categories. The original screening pool, original study, prior development questions and first repair cohort contained none of these full prompts. All six final submissions used separate Chrome contexts, the unchanged form and frontend build, a newly migrated application database with only the 23 normal directory entries, real provider and retailer routes, the shared ledger and the existing source and spending limits. Each request began with zero catalog offers. The first 18 submissions and their retrospective page checks remain in the [first-round report](../README.md).

## What each buyer saw

| Series | Category / entry | Request and trace | Search found a product URL | Queries by retailer | Priced listing shown | Verified | Final (s) |
| --- | --- | --- | ---: | --- | --- | ---: | ---: |
| Untouched | audio / exact model | [How much is a new black JBL Live 670NC delivered to Sidon?](runs/N1z.json) · [UI](runs/N1z.png) | 1 | technodel `JBL Live 670NC`, itworks `JBL Live 670NC` | No priced listing | 0 | 70.886 |
| Untouched | phone / exact model | [Please price a new OnePlus Nord CE5 with 256GB storage for delivery to Tripoli.](runs/N2z.json) · [UI](runs/N2z.png) | 0 | technodel `OnePlus Nord CE5`, beytech `OnePlus Nord CE5`, mojitech `OnePlus Nord CE5` (3 attempts) | No priced listing | 0 | 45.054 |
| Untouched | laptop / needs | [For university I need a laptop with at least 16GB RAM and a 512GB SSD, delivered to Batroun.](runs/N3z.json) · [UI](runs/N3z.png) | 1 | technodel `16GB`, beytech `laptop 16GB RAM 512GB SSD`, mojitech `laptop 16GB RAM 512GB SSD` (3 attempts) | Microsoft Surface Pro 12" 1st Edition Copilot+ PC, Snapdragon X Plus 8-Core, 16GB, 512GB SSD, Touchscreen, Win11H, Violet, Adapter Not Included (USD 1025, uncertain) | 0 | 80.365 |
| Untouched | monitor / needs | [Find me a monitor with at least 165Hz for gaming, delivered to Beirut.](runs/N4z.json) · [UI](runs/N4z.png) | 1 | technodel `165Hz`, beytech `monitor 165Hz gaming`, mojitech `monitor 165Hz gaming` (3 attempts) | Odyssey G90XF (USD 1895.3, uncertain) | 0 | 67.177 |
| Untouched | tablet / needs | [I need a Wi-Fi tablet with at least 128GB of storage for reading textbooks, delivered to Zahle.](runs/N5z.json) · [UI](runs/N5z.png) | 1 | technodel `128GB` | Apple iPad Air MH304 &#124; M4 &#124; 11" &#124; Wi-Fi &#124; 128GB &#124; Space Gray (USD 750, uncertain) | 0 | 59.424 |
| Development replay | monitor / needs | [Please find a monitor for my office with a minimum refresh rate of 100 Hz, delivered to Saida.](runs/C4z.json) · [UI](runs/C4z.png) | 1 | technodel `100Hz`, beytech `monitor 100Hz office`, mojitech `monitor 100Hz office` (3 attempts) | Samsung 27" Essential Monitor S3 S30GD 100Hz Full HD &#124; LS27D300GAMXUE (USD 118.8, uncertain) | 0 | 52.011 |

## Independent page checks

After all six submissions, the operator reread the exact URLs through their admitted product operations. Displayed listings had priority. Other search results appear below when the registry permitted the request. Prices are listed amounts. Pickup stock, a guaranteed arrival and a delivered total require additional evidence. The [check records](retailer-checks.json) include response times, hashes, selected SKUs, prices, availability, condition and errors.

| Buyer runs | Retailer page | Same-page offer | HTTP / gate |
| --- | --- | --- | --- |
| C4z | [Samsung 27" Essential Monitor S3 S30GD 100Hz Full HD &#124; LS27D300GAMXUE](https://technodel.net/product/samsung-27-essential-monitor-s3-s30gd-100hz-full-hd-ls27d300gamxue) | USD 118.8 · InStock | 200 |
| N3z | [Microsoft Surface Pro 12" 1st Edition Copilot+ PC, Snapdragon X Plus 8-Core, 16GB, 512GB SSD, Touchscreen, Win11H, Violet, Adapter Not Included](https://technodel.net/product/microsoft-surface-pro-12-1st-edition-copilot-pc-snapdragon-x-plus-8-core-16gb-512gb-ssd-touchscreen-win11h-violet-adapte) | USD 1025 · InStock | 200 |
| N4z | [Samsung Odyssey 3D G90XF 27" 4K 165Hz Gaming Monitor](https://technodel.net/product/samsung-odyssey-3d-g90xf-27-4k-165hz-gaming-monitor-mvn85fxy) | USD 1895.3 · InStock | 200 |
| N5z | [Apple iPad Air MH304 &#124; M4 &#124; 11" &#124; Wi-Fi &#124; 128GB &#124; Space Gray](https://technodel.net/product/apple-ipad-air-mh304-m4-11-wi-fi-128gb-space-gray) | USD 750 · InStock | 200 |
| N1z | [JBL Live 670BT NC Bluetooth Headset, White](https://technodel.net/product/jbl-live-670bt-nc-bluetooth-headset-white) | USD 97.9 · InStock | 200 |

## Suitability and remaining failures

- The black JBL request reached a white product. The OnePlus request returned no matching product URL. Neither exact-model buyer saw a priced variant.
- The USD 1,025 Surface Pro listing establishes 16 GB RAM and a 512 GB SSD. It is a detachable PC. The title excludes the adapter and the evidence does not establish an included keyboard. The UI left tax unresolved.
- The USD 1,895.30 Odyssey monitor search reached a model-specific page. The buyer card explicitly left refresh-rate evidence unresolved, along with item-level delivery, tax and shipping. The independent page title and its attribute named “Refresh Rate” both established 165 Hz. The reader looks for “Refresh rate” and missed the capitalized label.
- The USD 750 iPad Air title identifies 128 GB and Wi-Fi. The reader left delivery and mandatory costs unconfirmed. It also treated the purpose “for reading textbooks” as an unresolved clause. That use case needs a suitability assessment.
- The separate C4z replay showed the Samsung S30GD at USD 118.80 with 100 Hz from the current selected page. The earlier C4f record on the identical question and listing URL had no priced card. Shipping, tax and delivery to Saida stayed unknown. This replay demonstrates the repair and stays outside the untouched result.
- All priced final listings came from Technodel. This sample establishes no second-seller comparison or verified delivered total.

## Offline and CI checks

Python 3.11 passed 1,129 tests in 486.673 seconds. All 33 frontend tests passed and the production build succeeded. The final product revision passed both required CI jobs, including database testing and the Docker build. The original #178 cohort verifier still reports 43 screened and 20 frozen. [Pinned build CI](https://github.com/han33-dotcom/TechScout/actions/runs/45699649040)

## Accounting

- Untouched confirmation used USD 0.006838 settled, USD 0.0614400 uncertain and USD 0 held.
- Development replay used USD 0.001748 settled, USD 0 uncertain and USD 0 held.

Use `python acceptance/development/discovery-repair/verify_evidence.py` from the repository root to check all archived run IDs, request provenance, cohort membership, product pins, listing records and screenshots.
