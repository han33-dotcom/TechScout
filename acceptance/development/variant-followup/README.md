# Selected variants, specifications and delivered prices

## Development references and first losses

The first black H151 submission to Saida produced zero cards. Both retailers returned the independently identified product URL for the query `Logitech H151`. The failure occurred after link selection.

- At ITWorks Lebanon the enabled one-unit purchase form selects variant `52683501961447`, with no alternative options, at USD 28. Its product page describes a black wired headset with a single 3.5 mm jack. The reader required the USB Type-A heading from a different headset and discarded the selection. The item offers delivery to Lebanon for USD 5. The FAQ confirms nationwide service, a flat USD 5 fee added at checkout, and tax included in USD prices. The verified payable amount for one unit to Saida is USD 33. [Selected product](https://shop.itworkslb.net/products/logitech-headset-h151-815513) · [Published terms](https://shop.itworkslb.net/faq)
- At Technodel the selected H151 is black, SKU `RK-M2ND1CRV`, with three units recorded at USD 16 in the independent read. The heading puts the model after “Headset”. The original reader used the full merchandise title for exact matching. The repaired reader binds the model to that single SKU, corroborated brand, finish and wired connectivity. It keeps tax unknown on this layout. The buyer can see a USD 20 subtotal including USD 4 shipping. [Selected product](https://technodel.net/product/logitech-logitech-wired-audio-jack-headset-h151-black-981-000587)
- A separate black H111 submission to Jbeil reached Technodel's in-stock SKU `RK-MVHFP2VJ` at USD 10. Its heading and selected short description identify the model. The bounded ITWorks search returned no H111 link. Technodel established availability independently of that search miss. The white H151 control has no independently confirmed white option. Both H151 reference selections were black. [H111 listing](https://technodel.net/product/logitech-logitech-wired-headset-h111-stereo-business-black-981-000593)

[Independent search responses, selected variants and terms](development-references.json) · [H151 before](runs/A1.json) · [H151 after](runs/A1r.json) · [Buyer screenshot](runs/A1r.png)

## Confirmation protocol

Six new buyer requests span five categories. F1–F6 were recorded at `e8f571d`. A freshness check found that F3 shares 144 Hz racing-game criteria with the earlier study. Before product edits or a monitor lookup, `5af1900` kept F3 as an overlap audit and reserved F7. The fresh denominator is F1, F2, F7, F4, F5 and F6. F3 is run separately. The product build is `7670e463614755ff891f98cee409d2f451bd9e39`. The served frontend JavaScript has SHA-256 `65f5e567954ab67b6443e65f339c57381ba043a3191535986a0610cebfb01e56`.

Each buyer used the ordinary Chrome form in a fresh session with category, entry mode and original wording only. Each series used a newly migrated application database with 23 retailer directory entries. Search began with zero offers. The worker used the same 300-second development active allowance as #195 and the ordinary shared accounting deadline and retailer limits. Chrome waited for the terminal card, saved the screenshot and captured the actual buyer-visible text and links. [Reservation](confirmation.json)

A request counts as priced if at least one linked current price appeared. A configuration match requires current evidence for the requested model and finish or capacity on exact requests, or all stated hard product specifications on needs requests. A delivered total additionally requires a verified payable offer for the requested city. These are per-request counts. A listed price or a subtotal with missing tax cannot earn delivered-total credit.

## Earlier exact misses

A retrospective check found that Technodel offers the black JBL Live 670NC under its broader Audio category. The retailer page shows black, an in-stock purchase selection, SKU JK-Z84J89EQ and USD 111. The original N1 search reached a white headset. This remains an exact discovery gap. For N2 an exact 8 GB / 256 GB OnePlus Nord CE5 page exists at OutGeeked, but its selected option shows sold out. A purchasable domestic option for that request remains unconfirmed. These retrospective observations earn no buyer-run credit. [Technodel JBL](https://technodel.net/product/jbl-live-670nc-blk-wireless-overear-headphones-cs0637-go0154678) · [OutGeeked OnePlus](https://outgeeked.net/products/oneplus-nord-ce5)

## Development submissions

| Inputs | Requests | With a priced listing | Confirmed configuration or hard product requirements | Verified delivered total |
| --- | ---: | ---: | ---: | ---: |
| Original H151 black, H151 white, H111 black | 3 | 0 | 0 | 0 |
| Same three after the repair | 3 | 2 | 2 | 1 |
| Replays of the three previous needs-based cases from #195 | 3 | 3 | 3 | 0 |

The black H151 buyer saw two sellers at the exact links above, with one verified total. The white H151 buyer still saw zero cards. The ITWorks selection was black, and no checked seller established a white selection. The black H111 buyer saw Technodel at USD 10, with delivery, tax and shipping unresolved. ITWorks returned no matching H111 link. The original and repaired attempts are archived separately. [White control](runs/A2r.json) · [H111 replay](runs/A3r.json)

- The Odyssey G90XF card showed USD 1,895.30, 165 Hz and advice about graphics output at the buyer's resolution. Its receipt binds the original “Refresh Rate” field to SKU HA-ER2NZJBC. Tax remained unknown. [Monitor replay](runs/R4r.json)
- The USD 750 iPad Air card showed Wi-Fi and 128 GB for the textbook request. It advised checking full-page readability and described storage for downloaded material. There was no unresolved textbook clause. Tax and delivery remained unknown. [Tablet replay](runs/R5r.json)
- The USD 1,025 Surface Pro card showed 16 GB RAM and 512 GB SSD and asked the buyer to check keyboard inclusion and separate cost. Destination service and mandatory charges remained unverified. [Laptop replay](runs/R3r.json)

Offline, a selected 165 Hz display passed a 144 Hz minimum and failed 180 Hz. A value in kHz and conflicting label values yielded unknown. Both the purchase-component layout and a separate JSON-LD layout were exercised. On the same 128 GB Wi-Fi tablet, requiring 256 GB or cellular rejected the offer. A frontend test changes the buyer's purpose on the same device and checks that the reading advice changes with it.

## Untouched browser results

| First submission | Retailers attempted / successfully read per UI | Current priced listing | Confirmed hard product requirements | Verified delivered total |
| --- | ---: | --- | --- | --- |
| [F1 black Soundcore Q20i to Jounieh](runs/F1f.json) | 5 / 0 | None | No | No |
| [F2 Galaxy A26 5G, 128 GB, to Sidon](runs/F2f.json) | 4 / 0 | None | No | No |
| [F7 monitor, at least 120 Hz, to Byblos](runs/F7f.json) | 3 / 1 | [Technodel Samsung S31F](https://technodel.net/product/samsung-essential-monitor-s3-22-inch-full-hd-120hz-flat-monitor-s31f), USD 90 | No. The typed refresh field was unknown and the finalist recheck failed. The independent page confirmed 120 Hz at 1080p via HDMI. Its refresh-rate value starts with “Up to 120 Hz” and continues with prose. | No |
| [F4 Wi-Fi tablet, at least 256 GB, to Beirut](runs/F4f.json) | 4 / 0 | None | No | No |
| [F5 laptop, at least 64 GB RAM, to Tripoli](runs/F5f.json) | 4 / 1 | [Technodel ASUS ROG Zephyrus G16](https://technodel.net/product/el-ultra-9-rtx5090-laptop), USD 5,438.99 | Yes. The selected SKU established 64 GB installed RAM. The card also identified the GPU. Application-level rendering performance was unverified. | No. Item delivery, shipping and tax remained open. |
| [F6 black Logitech H540 to Batroun](runs/F6f.json) | 5 / 1 | None | No | No |

**Six first submissions produced 2/6 priced results, 1/6 confirmed hard product matches and 0/6 verified delivered totals.** The three exact requests produced 0/3 across all three measures. The three needs requests produced 2/3 priced, 1/3 confirmed hard requirements and 0/3 totals. Both priced cards came from Technodel. The worker also attempted ITWorks, Beytech, Class Cellulars and Mojitech across these six runs. The per-run files distinguish checked pages, failed reads and unvisited leads.

### Availability behind the misses

- F1's ITWorks URL contains “blue”, but the page's selected purchase form identifies a white Q20i. Separately, Beirut merchant IT Promax's form showed the black selection (3034) in stock at USD 48 and its product data marked it new. The fee for Jounieh was unverified. [ITWorks page](https://shop.itworkslb.net/products/soundcore-q20i-blue-771762) · [IT Promax selection](https://itpromax.store/en/p/soundcore-q20i-anc-headphones)
- F2 preserved the exact 128 GB requirement. Technodel returned an open-box A27. At IT Promax both 128 GB A26 options explicitly had no stock, while a black 256 GB option had stock. The 128 GB option stayed unconfirmed in the independent checks. [IT Promax variants](https://itpromax.store/en/p/samsung-galaxy-a26) · [Item-level records](availability-checks.json)
- F4 correctly interpreted Wi-Fi, at least 256 GB and the reading purpose. Its broad 256GB search returned no tablet link. An independent read confirmed a new Wi-Fi 256 GB iPad Air at Technodel at USD 840, SKU AZ-RGCXQ42H, in stock. [Technodel tablet](https://technodel.net/product/apple-ipad-air-11-inch-m4-wi-fi-256gb-2026)
- F6 reached a black H540 at Technodel. The independent read found SKU RK-95T7S1VA, eight units and USD 36. The heading includes “with On-Ear Controls”, outside the checked title forms. Exact matching discarded the merchandising title. GCS also lists the black 981-000480, with VAT extra. [Technodel selection](https://technodel.net/product/logitech-logitech-wired-h540-headset-with-on-ear-controls-black-981-000480) · [GCS listing](https://gcscomputerpro.com/shop/products/logitec-981-000480-h540-stereo-headset-usb-black)

## Overlap audit, validation and limits

The separate [F3 monitor audit](runs/F3f.json) produced 0/1 priced cards, 0/1 confirmed matches and 0/1 delivered totals. The linked ASUS monitor did have a 144 Hz field, but its selected condition was open box. New was the buyer default. All 16 original submissions and their screenshots are preserved.

The confirmation traces carry the suffix `+uncommitted`. Two test files were edited after the product pin. The running backend and configuration matched the pinned trees, there were no untracked product files, and the served JavaScript matched the pre-confirmation SHA-256. The trace marker is preserved in every record. Two independent post-case page reads used the shared ledger during the confirmation series. The Technodel A27 reread finished roughly five seconds before F7 began. Its request counted against the same minute limit; the record does not establish the cause of F7's failed finalist recheck. The remaining shared-ledger product rereads followed all browser submissions. Additional independent merchant lookups began only after the corresponding case finished and supplied no data to the app. [Build check](build-check.json) · [Returned URLs and purchase data](confirmation-pages.json)

The 35 focused backend regressions and all 34 frontend tests passed, and the production frontend built. The initial pinned backend suite passed 1,133 tests in 496.056 seconds; a further contrasting tablet regression was added afterwards. Both required CI jobs passed on the product commit. The #195 verifier still recognizes all 24 original submissions. The #178 validator preserves 43 screened inputs, 20 frozen cases and five comparison pairs. [Pinned build CI](https://github.com/han33-dotcom/TechScout/actions/runs/45708589904)

Reported settled buyer spend was USD 0.002652 for three development baselines, USD 0.010368 for six replays, USD 0.007460 for six fresh requests, and USD 0.000892 for the overlap audit. All 16 reported zero held and zero uncertain spend in their archived snapshots.

Run `python acceptance/development/variant-followup/verify_evidence.py` from the root to verify cohort membership, distinct submissions, build markers, zero starting catalogue offers, screenshots, links, current price/stock receipts and delivered-total arithmetic.
