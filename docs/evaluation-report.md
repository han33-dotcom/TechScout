# Domestic held-out evaluation

The domestic purchase decision applies to all baseline comparisons. Of the original 30 requests, 23 concern buyers purchasing from a seller in Lebanon. These cover seven phones, seven laptops, three tablets, three monitors and three audio requests. Fourteen use exact-product entry and nine describe buyer needs. The cohort includes 14 English, five Arabic and four Arabizi requests. Each original request retains its ID and wording. [Scope decision](https://github.com/han33-dotcom/TechScout/issues/155) · [Versioned protocol](evaluation-protocol.md)

## Planned comparisons

| Configuration | Domestic cases | Repetitions per case | Planned runs or sessions |
| --- | ---: | ---: | ---: |
| Full system, warm corpus | 23 | 3 | 69 |
| Single agent, warm corpus | 23 | 3 | 69 |
| Fixed heuristic, warm corpus | 23 | 3 | 69 |
| Full system, cold corpus | 23 | 1 | 23 |
| LebTech, One Souq and manual shopping | 10 per method | 1 | 30 total |
| ChatGPT browsing and Claude browsing | 10 per assistant | 1 | 20 total |

The initial schedule contains 230 automated trials and 50 external or manual sessions. Repetition reductions follow the frozen budget rule before the first trial. The ten cases for each external method retain their original inputs and pairing orders. Warm and cold orders keep the relative order from the 30-case schedule. Run `python -m eval.scope` for the current counts, including any recorded pre-run reduction. [Runner guide](../eval/runner-guide.md)

## Retired scenarios

H07, H09, H11, H18, H22, H26 and H30 require international purchase scope. They have no scheduled runs or outcome classifications. They contribute to none of the domestic request, offer, paired price or latency denominators. The original freeze and its schedules remain available at the [30-case checkpoint](https://github.com/han33-dotcom/TechScout/tree/3be08f4ea285aa61c0dc4063cf3defacc6ebf255/eval).

## Measurement status

At the scope amendment, zero held-out trials and zero external baseline sessions had been performed. Success, abstentions, relative price, coverage, latency and cost remain unmeasured. The 27 earlier buyer submissions belong to the separate development calibration. After calibration and the pre-run freeze, report observed and missing sessions for each configuration, followed by the outcomes and their applicable denominators. [Development measurements](development-calibration.md) · [Study issue](https://github.com/han33-dotcom/TechScout/issues/82)
