# Domestic held-out evaluation

The domestic purchase decision applies to all baseline comparisons. Of the original 30 requests, 23 concern buyers purchasing from a seller in Lebanon. These cover seven phones, seven laptops, three tablets, three monitors and three audio requests. Fourteen use exact-product entry and nine describe buyer needs. The cohort includes 14 English, five Arabic and four Arabizi requests. Each original request retains its ID and wording. [Scope decision](https://github.com/han33-dotcom/TechScout/issues/155) · [Versioned protocol](evaluation-protocol.md)

## Planned comparisons

| Configuration | Domestic cases | Repetitions per case | Planned runs or sessions |
| --- | ---: | ---: | ---: |
| Full system, warm corpus | 23 | 3 | 69 |
| Single agent, warm corpus | 23 | 3 | 69 |
| Fixed heuristic, warm corpus | 23 | 3 | 69 |
| Full system, cold corpus | 23 | 1 | 23 |
| LebTech and One Souq | 10 per service | 1 | 20 total |
| ChatGPT browsing and Claude browsing | 10 per assistant | 1 | 20 total |

Version 1.3.0 schedules 230 automated trials and 40 external slots, of which 20 can be operated on the public comparison sites. Human shopping, human buyer effort and independent human verification are unmeasured. The four external methods retain their ten cases and fixed inputs. Warm and cold orders keep the relative order from the 30-case schedule. Run `python -m eval.scope` for the current counts. [Runner guide](../eval/runner-guide.md)

## Retired scenarios

H07, H09, H11, H18, H22, H26 and H30 require international purchase scope. They have no scheduled runs or outcome classifications. They contribute to none of the domestic request, offer, paired price or latency denominators. The original freeze and its schedules remain available at the [30-case checkpoint](https://github.com/han33-dotcom/TechScout/tree/3be08f4ea285aa61c0dc4063cf3defacc6ebf255/eval).

## Measurement status

On November 24 both PostgreSQL study templates were restored and cloned on the primary laptop. Each has the same 28 directory entries and schema revision 0009. The warm copy holds 16 documents, 68 observations, 50 passages and 48 embeddings. The cold copy has zero of each. The warm dump restored with all 16 evidence files matching their recorded SHA-256 values. Test writes to each trial stayed outside its source template. Both templates exclude private buyer runs and sessions. The dated receipt records the dump hashes, evidence tree hashes and row counts. [Corpus preparation receipt](evaluation-corpus-2026-11-24.json)

The warm snapshot includes 10 documents from Beytech, two from OutGeeked and four from Wincell. Source admission and bootstrap used 43 retailer fetches and eight paid calls for USD 0.00001562. Maintenance used five retailer fetches and four paid calls for USD 0.00088698. These are acquisition costs incurred before the study. [Source counts and ledger references](evaluation-corpus-2026-11-24.json)

The first paired case is H15. Its local sequence is TechScout, One Souq and LebTech. Both consumer assistant arms retain their original denominator and have zero executable sessions. LebTech and One Souq exposed public search inputs. On November 24 the study browser showed signed-in ChatGPT Pro and Claude Free. [First-session instructions](../eval/runner-guide.md#first-paired-session)

At the scope amendment, zero held-out trials and zero external baseline sessions had been performed. Success, abstentions, relative price, coverage, latency and cost remain unmeasured. The 27 earlier buyer submissions belong to the separate development calibration. After calibration and the pre-run freeze, report observed and missing sessions for each configuration, followed by the outcomes and their applicable denominators. [Development measurements](development-calibration.md) · [Study issue](https://github.com/han33-dotcom/TechScout/issues/82)
