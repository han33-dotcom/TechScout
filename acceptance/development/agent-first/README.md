# Agent-first development checks

These requests and expectations precede the behavior change. Only the buyer fields in requests.json go into the UI. Independent reference links, expected behaviors and historical results stay outside the app. Each submission starts a new session with default new condition.

| Request | Expected buyer-visible behavior |
| --- | --- |
| D-phone, original development request | Keep A07s as written, search for the exact designation and inspect a plausible reviewed listing. Show only evidenced fields. The separate historical pasted-link run established a Class Cellulars listing. |
| D-audio, available exact model | Find the black Tune 720BT, link the exact seller listing and show supported price and stock, with any missing delivery charge explicit. [Ayoub listing](https://ayoubcomputers.com/jbl-tune-720bt-wireless-over-ear-headphones-black-t720btblk/) |
| D-tablet, genuine ambiguity | Ask which keyboard case or compatibility identifier the buyer has. Size and generation can change which tablet fits. Avoid claiming compatibility before identification. |
| D-monitor, unavailable configuration | Preserve both explicit requirements. The P2425H has a 23.8-inch panel. Explain the conflict with evidence or state which fact could not be checked. A different 32-inch model does not qualify. [Dell specifications](https://www.delltechnologies.com/asset/tr-tr/products/electronics-and-accessories/technical-support/dell-24-monitor-p2425h-datasheet.pdf) |

## Experimental scope

The owner selected a single live agent evaluation for #202. The 20 primary inputs, listed order, frozen references, scoring gates, budgets and per-case isolation carry forward from manifest-v2. Paired replay, alternating arms and a new control score are superseded. Both historical buyer UIs selected a real variant. Only the agent demonstrated general search through its buyer UI. The control's planned Tavily call failed before dispatch. Both preflights demonstrated managed scraping. The six reserved inputs remain sealed for separate confirmation. An agent score can establish absolute buyer correctness under these conditions. It cannot establish a paired architecture advantage.

## What happened on the original phone search

At 15:59:10 UTC the buyer entered “Samsung Galaxy A07s” with delivery to Batroun. The agent at c9c7103 searched SerpApi for “Samsung Galaxy A07s Lebanon retailer”. Its eight results included the Class Cellulars **A07** page. The manufacturer directory returned five retailer homepages. A second SerpApi search for “Samsung Galaxy A07s Lebanon price” returned eight hits, including MT Store's “Samsung A07 S 4/64”. No search returned the Class Cellulars A07s URL. The fourth action asked the buyer to choose A07 or A07s. No retailer listing was opened or inspected. The buyer received no offer. [All queries, titles, public URLs, actions, timing and spend](original-trace.json)

The pinned code offered inspection actions only for leads with a reviewed source ID. MT Store was absent from the 23-retailer directory. Its title was omitted from the model's observation, which reported the overall lead count. The recording establishes a discovery miss at the reviewed seller, loss of an unreviewed title and a clarification before product inspection. It cannot establish the model's private reasoning or what the MT Store page would have proved. A separate pasted-link run inspected the Class Cellulars A07s variation and reported USD 165 online. That receipt establishes a missed opportunity in the original search. [Pasted-link evidence](../paired-shopping/receipts/README.md)

## Development iterations

| Build | What happened on the phone request |
| --- | --- |
| 5236f98 | Added the unreviewed search titles as leads and offered quoted, seller-scoped model searches. SerpApi returned an error for one scoped search and timed out on another. The agent inspected an A07 page, tried Brave, then failed to complete a decision. The buyer saw no offer. [First attempt](first-attempt/D-phone.json) |
| d6a61ef | Exposed the existing bounded mobile-category catalog endpoint for exact phone searches. The agent found the exact page and selected Black and Light Violet. A further color change failed its product cross-check. Earlier selections had been superseded, and the buyer saw no offer. [Second attempt](second-attempt/D-phone.json) |
| 40320a1 | The agent exposes current checked partials while research continues. The decision instructions preserve a fitting selection while exploring other sellers or unresolved costs. A later invalidating observation still withdraws a partial. The buyer saw the black A07s at USD 165 with online stock, a Class Cellulars link and unresolved shipping. First priced display 53.461 s, final 74.953 s. [Final development receipt](verification/D-phone.json) |

Each attempt directory contains all four buyer submissions and a runtime lock. Titles from unreviewed sellers cannot create inspection permissions or verified offers. The separate audio reference found 961Souq's black JBLT720BTBLK-SD23-JAA listed in stock at USD 46. Its online availability does not establish a delivered total to Sidon. [Independent audio check](audio-reference.json)

### Final buyer-form verification

| Development request | What the buyer saw on 40320a1 | Final (s) | Settled USD |
| --- | --- | ---: | ---: |
| [A07s to Batroun](verification/D-phone.json) | Black, online stock, USD 165 and a working Class Cellulars link. The shipping charge, condition and payable total remained unresolved. | 74.953 | 0.0257685 |
| [Black Tune 720BT to Sidon](verification/D-audio.json) | No priced link. The agent reported a color mismatch at ITWorks. Its separate read of the black product URL hit a budget limit. A matching black pair was independently available. | 102.344 | 0.0688645 |
| [Keyboard-compatible iPad Air](verification/D-tablet.json) | Asked for the keyboard's brand and model number or compatibility text before recommending a tablet. The question addressed the missing fact. | 7.891 | 0.0037185 |
| [32-inch Dell P2425H](verification/D-monitor.json) | No offer. The UI gave a generic explanation and omitted the 23.8-inch panel conflict. | 79.406 | 0.0436240 |

All four settled with zero held or uncertain spend. The final development run demonstrated the phone partial and a justified question across buyer scenarios for exact models, incomplete charges and clarification. The available audio model and evidenced monitor blocker remain buyer-visible failures. [Buyer acceptance matrix](../../../docs/buyer-acceptance-matrix.md) · [Runtime settings](verification/runtime.json)

## Reproduction

With the repository's Python 3.11 environment, Chromium and Node installed, run the commands below from the repository root. Supply a new work directory and the control seed prepared by the paired runner. Preparation verifies the original logical catalog at migration 0009, builds the specified candidate from its Git archive, then applies the candidate's account schema migration. The 23 directory entries carry forward with empty offer, evidence, buyer, account and saved-location tables. Each submission copies the closed seed. The backend receives one exact buyer request and never receives the grader references.

```text
python acceptance/development/agent-first/evaluate.py prepare --revision 40320a1 --work var/agent-first --seed var/paired-dev/seed.sqlite
python acceptance/development/agent-first/evaluate.py development --work var/agent-first
```

The separately authorized primary command is `python acceptance/development/agent-first/evaluate.py primary --work var/agent-first`. It uses the shared submission clock. Each accepted original retains its buyer text, visible links, screenshot, display timeline, tool events, response-bank digest, controller trace and per-run accounting. An incomplete output directory requires reconciliation before resuming. The driver has no confirmation mode.
