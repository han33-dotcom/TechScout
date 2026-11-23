# Buyer walkthroughs

Use this guide with three to five people who buy electronics in Lebanon. The purpose is to observe whether a buyer can choose a configuration and seller, explain what they would pay, and tell when there is too little evidence to choose. [Issue #81](https://github.com/han33-dotcom/TechScout/issues/81)

## Before each session

Start the buyer app and worker and open a fresh private browser session. Record the app commit, session date and whether the session uses live sources or synthetic fixtures. Use the published development cases below. Keep the held-out evaluation cases reserved for the study. [Local buyer setup](domestic-buyer-demo.md) · [Development cases](../eval/calibration.json)

Ask permission to take anonymous notes. Use a session code such as P01. A city or public branch code is enough for these tasks. Omit names, contact details, precise addresses, browser session tokens and account information from the notes. Tell the buyer they can stop at any time and that the exercise ends at the retailer handoff.

## Tasks to read aloud

Give each buyer both an open-ended purchase and a specific product. Alternate which comes first. Let the buyer choose controls and explain decisions in their own words. Keep the facilitator cues in the right column private.

| Task | Read to the buyer | Facilitator cues |
| --- | --- | --- |
| Engineering laptop | “You want a new laptop for engineering classes, delivered to Beirut. You have around USD 900 in mind. You need 16 GB of RAM and care about battery life. Use TechScout to decide what you would consider.” | Observe how the buyer enters an approximate budget. If the app asks about required software, the scenario answer is “I use AutoCAD and MATLAB on Windows.” Record whether a material clarification appeared and what changed afterward. |
| Refine the laptop search | “You will carry it to university every day. Battery life now matters more than the lowest price. Change the search and show me what you would rely on now.” | Observe how they distinguish the earlier result from the current revision, a suitable model from a seller, and an early candidate from a verified result. |
| Exact phone | “You want a new 256 GB iPhone 17 delivered to Beirut. Find out which sellers you would consider and what you expect to pay.” | The exact-model flow aims to check two sellers for the same variant. Ask what was checked, which charges remain unknown and what local dispatch establishes about delivery to Beirut. Record the number actually displayed. |
| Retailer link and pickup | “Someone sent you this phone listing. See what you can establish about this particular offer. Now suppose you can collect it only from one branch. Show me how you would change the request and where you would go next.” | Supply the Mojitech URL from development case C13 and a branch code published by the retailer if available. Observe whether the buyer distinguishes a link to one listing from a market comparison, requires evidence for stock at the chosen branch, and can identify the retailer handoff. |

Ask “What on this screen led you to that decision?” and “What, if anything, would you still need to check?” before explaining labels. With one or zero qualifying offers, ask what the buyer would do next. For a needs search, observe whether they distinguish up to three distinct models from multiple sellers of one configuration. If a state never appears, record **not observed**. A separately labelled demonstration can exercise that state. Keep its evidence separate from the participant's original attempt.

## Anonymous observation sheet

Copy one row per task actually attempted. Record the buyer's action and explanation before the facilitator's interpretation. A correct decision to withhold a purchase can complete a task.

| Session / task | Input, source mode and revision | Outcome (independent / assisted / stopped) | What the buyer did or said | Evidence or cost they understood or missed | Prompt or help given |
| --- | --- | --- | --- | --- | --- |
| | | | | | |

In the session summary, note which states the buyer saw, how they distinguished provisional and historical information, which seller/variant/branch they selected, and where they expected to complete a purchase. For each material difficulty, link the observation to the affected screen, the fix and a subsequent check. Report counts with the number of people who actually encountered that state.

## Facilitator rehearsal

A browser rehearsal used the bundled UI, the owned-run API and the buyer worker with the existing domestic matrix's synthetic retailer pages and model responses. The laptop search showed one configuration with 16 GB RAM. Its two sellers appeared side by side at USD 104 and USD 105 including shipping. A phone listing for pickup at the fixture branch `hamra` qualified at USD 99. Changing only the branch to `verdun` produced no verified recommendation in revision two. The original offer stayed under the previous revision.

The rehearsal exposed a missing branch field in the buyer form. The form now accepts the retailer's branch code and removes that constraint when the buyer switches to delivery. Pickup offers label unknown readiness as pickup timing. An alternative with unconfirmed dispatch now says its origin is unknown in both the offer details and the written summary. The underlying fixture scenarios can be checked with `python -m unittest discover -s tests -p test_domestic_flow_matrix.py -v` and the form with `npm --prefix frontend test`. Participant observations are still pending.
