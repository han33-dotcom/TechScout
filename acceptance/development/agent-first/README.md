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
