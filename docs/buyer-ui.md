# Buyer workspace

The React workspace uses the owned [buyer API](buyer-api.md) to submit, poll, clarify, cancel and refine a shopping request. An edit creates a new revision while preserving untouched constraints. Previous results remain available as history.

## What the buyer sees

- Progress and source status for the current revision. A source check records coverage. Each verified offer also needs its own current-run receipts.
- Up to three model groups with sellers under each configuration. Cards show the variant, seller, terms, cost breakdown, evidence dates and listing link. Comparisons order exact decimal totals within a currency.
- A separate view for incomplete or uncertain offers, including missing charges. Historical results retain their original timestamps.
- A plain-text explanation when the worker supplies one. The backend owns verification, ranking and currency conversion. [Writer](writer.md)

## Developer reference

`ShoppingRequest.refinement_note` carries extra buyer text. `RunSnapshot.source_outcomes` records checked, failed or unchecked sources. Optional `RunSnapshot.explanation` holds the text, evidence IDs and source. Older snapshots remain readable with their defaults.

Run `python -m app.api_types` to regenerate the browser types and normalized test fixture. Browser interaction tests use synthetic API responses. The [acceptance matrix](buyer-acceptance-matrix.md) specifies where a buyer-visible walkthrough or live result is required. [Session history and export](buyer-history.md)
