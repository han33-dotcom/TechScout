# Single shopping agent on the frozen 20

**The agent failed buyer acceptance.** The 20 original requests produced 0 fully verified answers, 0 qualifying partials and 4 specific blocker explanations in their respective groups. There were 0 comparable seller pairs and 0 misleading verified claims. Product acceptance #178 and the buyer-flow milestone #76 stay open. [Machine-readable scorecard](scorecard.json) · [Buyer protocol](../../../docs/buyer-correctness-acceptance.md)

| What the buyer needed | Earned | Required |
| --- | ---: | ---: |
| Complete answer with a verified payable total | 0/10 | 8/10 |
| Matching priced partial with a working link and a specific gap | 0/5 | 4/5 |
| Explanation of the evidenced blocking constraint | 4/5 | 5/5 |
| Two comparable sellers for the same variant | 0/5 | 4/5 |
| Misleading verified claims | 0 | 0 |

## What reached the buyer

The trace located a correct product page on 7/20 requests. This conservative count includes a confirmed search-result URL, a parent page offering the requested option, or an exact model out of stock. Each qualifying row names the evidence. The UI confirmed a model and its requested hardware on 1/20, showed a current listed price and working seller link on 2/20, and showed 0 verified payable totals. These counts include intermediate displays.

The clearest loss occurred on B31. At 36.230 seconds the buyer could open an in-stock Technodel HP OmniBook with the requested 32 GB RAM and 1 TB SSD at USD 1,448. Tax was unresolved. A browser inspection of the same lead failed, the card disappeared at 45.001 seconds, and the final display offered nothing. On B08 the USD 505 Technodel link persisted, but the required numeric keypad remained unconfirmed. The four pickup cases produced no branch-stock confirmation. The B10 search header also interpreted 32 GB of built-in memory as storage. Its answer correctly explained the RAM limit. This visible extraction error is recorded separately from misleading verified offers. [B31 display timeline and actions](originals/B31.json) · [B08 final buyer screen](originals/B08.png)

## Latency and cost

| Measurement across the 20 originals | Observed |
| --- | ---: |
| First useful displays within 120 s | 5/20 |
| First useful display unreached | 15/20 |
| Median first useful time, attained cases only | 29.161 s |
| Final displays within 120 s | 20/20 |
| Final display, median (range) | 78.015 s (8.700–102.156 s) |
| Dispatched external calls | 236 |
| Settled USD | 0.6814960 |
| Uncertain USD | 0.1228800 |
| Still held USD | 0 |
| Distinct answers earning frozen class credit | 4 |
| Settled USD per class-appropriate useful answer | 0.1703740 |

Time starts at buyer submission. The first useful timer also captures a matching, actionable provisional offer on a full-class request. Cost per useful answer counts each original only when it earns its frozen class credit. Spend includes every original and excludes development, preflight and independent reference checks. Uncertain charges retain their full reservations. Per-case accounting separates external calls from internal work and reconciles to the durable ledger.

## Original submissions

Times are seconds from submission to the recorded DOM change. A pair flag marks one of the five predeclared comparisons within the ten full cases.

| Receipt | Final screen | Class | Credit | First useful | Final | Settled USD | Uncertain USD | Buyer result |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| [B07](originals/B07.json) | [UI](originals/B07.png) | full | No | Unreached | 86.349 | 0.0591935 | 0 | Opened the exact MLY33 MacBook Air at Technodel, where the capture showed USD 848 and out of stock. The buyer saw no listing, price or Tripoli total. The independently known Khalife offer was missed. |
| [B11](originals/B11.json) | [UI](originals/B11.png) | full | No | Unreached | 91.047 | 0.0732280 | 0 | Opened the matching blue 128 GB Wi-Fi A16 iPad at Technodel. The page showed USD 475 and in stock. The buyer saw no priced link or Beirut total. |
| [B17](originals/B17.json) | [UI](originals/B17.png) | full | No | Unreached | 91.715 | 0.0591260 | 0 | The agent opened a 32-inch G55C. General search also returned a 27-inch listing independently confirmed at 961Souq. The UI showed neither a qualifying product nor a Tripoli total. |
| [B21](originals/B21.json) | [UI](originals/B21.png) | full + pair | No | Unreached | 79.156 | 0.0565685 | 0 | Opened the multicolor JBL Tune 520BT parent page with Blue selected. Black was available as a distinct selection in the independent reference. Neither a black priced offer nor comparable Beirut totals reached the UI. |
| [B23](originals/B23.json) | [UI](originals/B23.png) | full + pair | No | Unreached | 50.617 | 0.0311820 | 0 | Search returned wired USB-C earbuds with a microphone, including a matching Phonefinity listing. The UI showed no price, seller link, Saida total or comparison. |
| [B27](originals/B27.json) | [UI](originals/B27.png) | full | No | Unreached | 102.156 | 0.0352890 | 0 | Opened an ASUS VP249QGR with the requested display specifications. The page listed USD 149 and out of stock. The buyer saw no alternative or Zahle total. |
| [B31](originals/B31.json) | [UI](originals/B31.png) | full + pair | No | 36.230 | 65.055 | 0.0366980 | 0 | A matching 32 GB / 1 TB HP OmniBook, USD 1,448 online with a Technodel link, appeared at 36.230 s. Tax was unresolved. The card disappeared at 45.001 s. The final UI gave no offer, Saida total or comparison. |
| [B35](originals/B35.json) | [UI](originals/B35.png) | full | No | Unreached | 76.874 | 0.0222585 | 0 | Native retailer search yielded a Galaxy A27. General search returned category or social pages. No S-series listing with an integrated S Pen, price, link or Beirut total reached the buyer. |
| [B36](originals/B36.json) | [UI](originals/B36.png) | full + pair | No | Unreached | 102.089 | 0.0627780 | 0 | Opened Lenovo Yoga ANC GS-7127 at USD 70. The retailer described wireless over-ear ANC. The captured listing did not establish USB-C charging for this SKU. No priced link or Jounieh seller comparison reached the UI. |
| [B39](originals/B39.json) | [UI](originals/B39.png) | full + pair | No | Unreached | 79.314 | 0.0586745 | 0 | The agent opened a used Lenovo Legion H600. The buyer requested new and saw no qualifying headset, link or Byblos total. |
| [B08](originals/B08.json) | [UI](originals/B08.png) | partial | No | Unreached | 91.184 | 0.0519935 | 0 | The UI showed the in-stock HP 14-EP2035CL at USD 505 with a Technodel link and explicit keypad and tax gaps. The built-in numeric keypad was a hard constraint and was never established. |
| [B24](originals/B24.json) | [UI](originals/B24.png) | partial | No | Unreached | 13.729 | 0 | 0.0614400 | The first model call had a transport error before search or retailer operations. No priced black JBL Tune 770NC or Hamra pickup qualification appeared. |
| [B37](originals/B37.json) | [UI](originals/B37.png) | partial | No | Unreached | 95.026 | 0.0542460 | 0 | The search returned monitor categories. No suitable 27-inch IPS USB-C product, price or Beirut pickup qualification appeared. |
| [B42](originals/B42.json) | [UI](originals/B42.png) | partial | No | Unreached | 83.456 | 0.0458750 | 0 | Search returned Android tablet categories. No suitable 11-inch or larger, 8 GB tablet with a priced retailer link and Tripoli pickup qualification appeared. |
| [B43](originals/B43.json) | [UI](originals/B43.png) | partial | No | Unreached | 8.700 | 0.004321 | 0 | Asked which Beirut shop or branch to check and explained that availability varies by branch. The UI gave no priced black 128 GB iPhone 15 link, so the branch question alone earns no partial credit. |
| [B05](originals/B05.json) | [UI](originals/B05.png) | blocked | Yes | 32.430 | 32.430 | 0.0081945 | 0 | Explained that Apple did not make an iPhone 15 mini. Asked whether to preserve the smaller size with a 13 mini or the generation with a standard iPhone 15. The independent Apple lineup supports the blocker. |
| [B10](originals/B10.json) | [UI](originals/B10.png) | blocked | Yes | 9.989 | 9.989 | 0.0045535 | 0 | Explained that the M1 MacBook Air supports 8 or 16 GB RAM and cannot meet the 32 GB requirement. The search summary mislabeled 32 GB as storage, but the answer explicitly addressed built-in memory and the model limit. |
| [B15](originals/B15.json) | [UI](originals/B15.png) | blocked | Yes | 28.931 | 28.931 | 0.0083395 | 0 | Explained that the A17 Pro iPad mini starts at 128 GB. Asked whether the buyer would change the storage requirement or accept an older generation. The Apple specifications support the explanation. |
| [B20](originals/B20.json) | [UI](originals/B20.png) | blocked | Yes | 29.161 | 29.161 | 0.008977 | 0 | Explained that the exact Dell P2422H has a native 1920 × 1080 panel and cannot provide native 3840 × 2160. Asked which requirement to preserve. Dell specifications corroborate the conflict. |
| [B25](originals/B25.json) | [UI](originals/B25.png) | blocked | No | Unreached | 10.025 | 0 | 0.0614400 | The agent failed on its first model call. The buyer received no explanation that the black MDR-ZX110 uses a wired stereo plug and cannot provide the requested adapter-free Bluetooth. |

## Candidate and scope

The owner authorized this single-agent live study through the 20 originals. The historical control and agent both selected a Class Cellulars variant and showed USD 165 with incomplete costs. The agent dispatched general search. The control planned Tavily and its worker failed before dispatch. Both passed the managed-scrape preflight. The paired buyer runs left branch-stock checks and pre-order quotes unverified. Control repair and paired replay were removed from the critical path. The study measures absolute buyer correctness for the agent on integrated main. A head-to-head architectural comparison would require paired evidence. [Owner decision on #201](https://github.com/han33-dotcom/TechScout/issues/201) · [Independent development cases and trace](../../development/agent-first/README.md)

The candidate was `40320a1519ee7972d92ee74cc10afeef1078dcee`, based on main `801129e`. The pin was recorded in `26704d4` after the development verifications and before the first original. The agent used `shopping-agent-3`, OpenRouter's `openai/gpt-6.1-sol`, the OpenAI provider, low reasoning effort and fallback disabled. Chromium 156.0.8078.4 displayed the pinned UI. The headless shell was build 1248, SHA-256 `f77d8e27c8db1e4ac0399459a00e0c97bb9417adc393474cabaffebe2314ecb0`. SerpApi, Brave, Firecrawl Search, Tavily, the manufacturer directory and Firecrawl Scrape passed the preflight. Every request started with its own anonymous browser context, an empty catalog and the same 23 reviewed retailer entries. The current product carried the accounts migration to schema 0010. The four pickup submissions preserved the city or neighborhood and left the optional branch field blank. [Run lock and hashes](run-lock.json) · [Runtime manifest](originals/runtime.json)

The active lease and browser cap were 300 s. The ledger allowed 120 s, 40 external calls and USD 1 per job. Research had USD 0.75 with 30 s, ten calls and USD 0.25 reserved for completion. The shared project cap was USD 500. The smallest gap between original submissions was 61.024 s. Each receipt retains the input, run ID, terminal UI, intermediate text, public seller links, tool actions, source hashes, bank digest and spend. The buyer UI, backend and worker handled the original wording, category, entry mode, fulfillment and destination on each accepted run. All 20 originals remain in their predeclared denominators. The six reserved requests received zero submissions. [Frozen cohort](../../buyer-v1/freeze-v1-2026-11-25.json) · [Original scope and thresholds](../manifest-v2.json)

## Independent references and drift

The original screening found 15 fully verifiable, 14 partial, five blocked and nine unclassified requests among 43 screened. Five comparison flags overlap the selected full group. The evaluator rechecked 32 public reference URLs separately from buyer runs. Twenty-nine returned HTTP 200 on the first pass. The GCS laptop page initially returned 503 and later confirmed USD 833. Two previously refused manufacturer hosts were left alone, with Dell and Sony specifications corroborated from their indexed manufacturer pages. Selected variant and seller policy evidence, alternative listing checks, receipts and unsuccessful attempts are retained. [Screening record](../../buyer-v1/review-v1-2026-11-25.json) · [Reference recheck](reference-recheck.json)

Six independent one-unit city quotes were refreshed. The B31 Technodel Saida quote remained USD 2,500, down from USD 2,520 at freeze. The other five matched their prior recheck. ITWorks' applicable delivery charge and tax policy were checked independently. The B21 CompuOne guest checkout failed to yield a fresh Beirut quote after two attempts. Its earlier USD 45 quote is dated evidence only. No case changed denominator. No reference URL, expected outcome or separately refreshed quote was passed to the agent.
