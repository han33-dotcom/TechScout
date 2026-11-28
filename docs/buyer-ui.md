# Buyer workspace

The React workspace uses the owned [buyer API](buyer-api.md) to submit, poll, clarify, cancel and refine a shopping request. An edit creates a new revision while preserving untouched constraints. Previous results remain available as history.

## What the buyer sees

- Progress and source status for the current revision. A source check records coverage. Each verified offer also needs its own current-run receipts.
- Up to three model groups with sellers under each configuration. Cards show the variant, seller, terms, cost breakdown, evidence dates and listing link. Comparisons order exact decimal totals within a currency.
- A separate view for incomplete or uncertain offers, including missing charges. Historical results retain their original timestamps.
- A plain-text explanation when the worker supplies one. The backend owns verification, ranking and currency conversion. [Writer](https://github.com/han33-dotcom/TechScout/blob/baseline/pre-scout/docs/writer.md)

## Accounts and saved locations

The [account backend](buyer-accounts.md) supports these pages.

- **Header.** It shows the signed-in username, or a button to sign in or create an account.
- **Account page.** The buyer registers, signs in and signs out. Passwords are cleared from the form after a successful request. Errors show the server's fixed message, never the submitted values. Sign-out warns first that it deletes this browser's searches.
- **Saved locations.** The page shows each location's label and city only. The street is never displayed after saving.
- **Address prompt in a chat.** The prompt names the chat's Lebanese sellers together, once. The buyer ticks which sellers may receive an address; none is ticked by default.
  - **Choose a saved address** opens Locations, through sign-in when needed. **Use this address** saves the choice and returns to the same chat with its earlier revision, offers and links. No search is resubmitted.
  - **Continue without an address** records a decline. Every offer, link and unknown fee stays as it was.
  - Until the quote operation in #186 exists, the prompt says that seller quotes have not been requested and totals are unchanged.
- **Address bar.** It holds the open chat and page (`?chat=<run id>&view=locations`), so a reload or Back returns to the same chat.

`Account.test.tsx` covers these flows with synthetic API responses. The scripted browser walkthrough and its screenshots are in [issue-187 evidence](https://github.com/han33-dotcom/TechScout/blob/baseline/pre-scout/acceptance/development/issue-187/evidence.json).

## Developer reference

`ShoppingRequest.refinement_note` carries extra buyer text. `RunSnapshot.source_outcomes` records checked, failed or unchecked sources. Optional `RunSnapshot.explanation` holds the text, evidence IDs and source. Older snapshots remain readable with their defaults.

Run `python -m app.api_types` to regenerate the browser types and normalized test fixture. Browser interaction tests use synthetic API responses. The [acceptance matrix](https://github.com/han33-dotcom/TechScout/blob/baseline/pre-scout/docs/buyer-acceptance-matrix.md) specifies where a buyer-visible walkthrough or live result is required. [Session history and export](buyer-history.md)
