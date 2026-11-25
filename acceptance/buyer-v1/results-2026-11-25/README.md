# Frozen buyer evaluation · 25 November 2026

**Acceptance failed.** All 20 original searches ended in an abstention. Buyers saw no priced product links, verified payable totals or explanations of the five known hardware contradictions. There were 0 misleading verified claims.

| Correctness measure | Passed | Required |
| --- | ---: | ---: |
| Fully verifiable requests | 0/10 | 8/10 |
| Useful partial results | 0/5 | 4/5 |
| Correctly explained blocked requests | 0/5 | 5/5 |
| Comparable two-seller results | 0/5 | 4/5 |

**Timing and spend.** 20/20 reached a final display within 120 seconds. The range was 19.724–42.997 seconds, with a median of 32.879 seconds. No search showed a useful result within 120 seconds or later. The shared ledger settled USD 0.022772, with USD 0 uncertain and USD 0 still held. [Machine-readable totals](totals.json)

## Original submissions in frozen order

Each case ID opens its submitted request, run ID, rendered text and timeline, retailer outcomes, worker phases and ledger evidence. Each capture shows the original browser result. “None” in the first-useful column means the buyer never saw an actionable listing or the specific blocking constraint. The final time uses the first rendered terminal status measured from the form submission. Every row reached the 120-second final-time target.

| Case and record | Capture | Frozen class | First useful (s) | Final (s) | Settled USD | Result and reason |
| --- | --- | --- | ---: | ---: | ---: | --- |
| [B07](B07.json) | [UI](B07.png) | full | None | 28.431 | 0.001066 | Fail. No Midnight M2 Air listing, current stock or Tripoli total. |
| [B11](B11.json) | [UI](B11.png) | full | None | 25.915 | 0.000996 | Fail. No blue 128 GB Wi-Fi A16 iPad listing or Beirut total. |
| [B17](B17.json) | [UI](B17.png) | full | None | 19.724 | 0.001026 | Fail. No qualifying Samsung G55C listing or Tripoli total. |
| [B21](B21.json) | [UI](B21.png) | full + pair | None | 32.136 | 0.000962 | Fail. No black JBL 520BT offer, seller link or comparison for Beirut. |
| [B23](B23.json) | [UI](B23.png) | full + pair | None | 32.033 | 0.001352 | Fail. No USB-C calling earbuds or delivered seller comparison for Saida. |
| [B27](B27.json) | [UI](B27.png) | full | None | 19.735 | 0.001104 | Fail. No qualifying DisplayPort gaming monitor or Zahle total. |
| [B31](B31.json) | [UI](B31.png) | full + pair | None | 20.840 | 0.00111 | Fail. No qualifying 32 GB / 1 TB notebook or two-seller total for Saida. |
| [B35](B35.json) | [UI](B35.png) | full | None | 19.768 | 0.001042 | Fail. No 256 GB S Pen phone listing or Beirut total. |
| [B36](B36.json) | [UI](B36.png) | full + pair | None | 42.067 | 0.001074 | Fail. No ANC over-ear model, Jounieh total or comparable seller pair. |
| [B39](B39.json) | [UI](B39.png) | full + pair | None | 42.997 | 0.001072 | Fail. No qualifying USB-receiver headset or pair of delivered totals for Byblos. |
| [B08](B08.json) | [UI](B08.png) | partial | None | 38.343 | 0.001114 | Fail. No priced numeric-keypad Windows laptop link or explanation of the missing VAT and delivery total. |
| [B24](B24.json) | [UI](B24.png) | partial | None | 42.987 | 0.000952 | Fail. No priced black JBL 770NC lead or item-specific Hamra pickup caveat. |
| [B37](B37.json) | [UI](B37.png) | partial | None | 34.743 | 0.001084 | Fail. No priced 27-inch USB-C IPS lead or Beirut branch-stock qualification. |
| [B42](B42.json) | [UI](B42.png) | partial | None | 33.418 | 0.001088 | Fail. No priced qualifying Android tablet or Tripoli branch-stock qualification. |
| [B43](B43.json) | [UI](B43.png) | partial | None | 39.437 | 0.001046 | Fail. No priced black 128 GB iPhone 15 lead or specific Beirut branch-stock evidence. |
| [B05](B05.json) | [UI](B05.png) | blocked | None | 32.340 | 0.001268 | Fail. The result did not explain that the iPhone 15 generation has no mini model. |
| [B10](B10.json) | [UI](B10.png) | blocked | None | 34.748 | 0.001462 | Fail. The result did not explain the M1 Air’s 16 GB memory limit. |
| [B15](B15.json) | [UI](B15.png) | blocked | None | 35.740 | 0.001274 | Fail. The result did not explain the A17 Pro iPad mini’s 128 GB minimum. |
| [B20](B20.json) | [UI](B20.png) | blocked | None | 27.160 | 0.001346 | Fail. The result did not explain that the P2422H panel is native 1920 × 1080. |
| [B25](B25.json) | [UI](B25.png) | blocked | None | 38.329 | 0.001334 | Fail. The result did not explain that the MDR-ZX110 uses a wired stereo plug. |

## Where the buyer flow failed

Discovery returned zero usable listings across the ten requests for which independent checks found a complete answer. No finalist reached price and stock verification. All five partial requests also lacked a priced link. In the five blocked cases, a generic account of failed retailer checks left the buyer without the reason the requested hardware combination was impossible.

B21 shows a concrete failure before an offer could be evaluated. Chrome submitted the entire 101-character question as the exact model. The audio search code uses that field as its retailer query. Both reviewed search endpoints limit the query to 64 characters. The question also contains punctuation outside their allowed search grammar. The B21 ledger recorded zero retrieval attempts from ITWorks or Technodel. Independently, the selected black JBL 520BT was USD 37.34 delivered from ITWorks and USD 45.00 from CompuOne. The buyer saw neither listing. The needs-based requests used a different discovery path and likewise produced no candidates. [Browser and ledger](B21.json) · [Pinned form](https://github.com/han33-dotcom/TechScout/blob/1c5f80c75a6a70d9eeaf6e8db08d81dfb5097c64/frontend/src/request.ts) · [Audio search](https://github.com/han33-dotcom/TechScout/blob/1c5f80c75a6a70d9eeaf6e8db08d81dfb5097c64/app/domestic_buyer_sources.py) · [Endpoint limits](https://github.com/han33-dotcom/TechScout/blob/1c5f80c75a6a70d9eeaf6e8db08d81dfb5097c64/config/sources.json)

## Run conditions and independent reference checks

The candidate was the merge of #192, following #190 and #191 on main. The frozen manifest order, configuration hashes, retailer directory seed and budgets were recorded before submission. A freshly built buyer UI sent all 20 requests through the ordinary FastAPI service to a separate domestic worker. Each original had a fresh browser session. The shared application database began with 23 directory records and an empty offer catalog, matching the checkpoint starting conditions. Each request contained the verbatim prompt and the frozen category, entry, location and fulfillment fields. The four pickup prompts supplied no branch code. Each kept its requested location and left the optional code blank. [Run lock](run-lock.json) · [Frozen cohort](../freeze-v1-2026-11-25.json) · [Scoring protocol](../../../docs/buyer-correctness-acceptance.md)

The development launcher set a 300-second active lease on both service processes. Each buyer job kept the 120-second ledger deadline, USD 1 spend ceiling, 40-call limit and USD 0.75 research allowance. The pinned source registry governed retailer access. All 20 originals have one accepted submission each and a terminal browser capture. There were no buyer reruns, timeouts, product changes or scoring substitutions. A pre-submission browser selector timed out before any buyer POST. The driver then used the form’s accessible combobox name.

The evaluator independently checked the listings, options, stock, published terms, one-unit guest quotes and manufacturer specifications. Technodel’s B31 MacBook Pro price and delivered Saida total changed from USD 2,520 to USD 2,500. ITWorks remained USD 3,113, increasing the difference to USD 613. The variant, cheaper seller and original denominator stayed the same. All 15 primary full-case listings and five primary partial-case listings were checked. Online stock in the pickup cases did not establish branch stock. For Dell and Sony the evaluator used indexed manufacturer specifications after prior direct-read refusals. The recheck record includes dated URLs, hashes for direct reads, quote lines and unsuccessful evaluator quote captures. [Reference recheck](reference-recheck.json)

The original screen classified 15 of 43 requests (34.9%) as fully verifiable, 14 as partial and five as blocked. Nine remained unclassified. The selected 20 measure performance in those fixed groups. The five comparison flags overlap the ten fully verifiable cases. The four failed thresholds leave #178 and parent #76 open. [Original screening decisions](../review-v1-2026-11-25.json)
