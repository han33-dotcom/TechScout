# Buyer workspace

The React/TypeScript/Vite buyer interface uses the generated v2 API types and same-origin BuyerApi. Start with `cd frontend`, `npm ci`, and `npm run dev`. Vite proxies `/api` to `http://127.0.0.1:8000` (override with TECHSCOUT_API_ORIGIN). Use the backend setup in buyer-api.md. A worker/controller is required for real shopping progress; a missing worker leaves a request honestly queued.

`npm run typecheck`, `npm test`, and `npm run build` run strict TypeScript checks, offline component/race tests, and the production build. CI runs these alongside the existing Python/PostgreSQL/Docker checks. FastAPI serves frontend/dist in the production stack. No provider keys, browser provider calls, external fonts or tracking scripts are used.

## Request and progress (#72)

Needs, exact-model and HTTPS product-link entry share editable five-category requirements. Lebanon/new condition and international-off are defaults. Target budgets remain preferences and a separate field sets an explicit maximum. Original text is retained; the frontend does not claim to understand arbitrary natural language. English, Arabic and Arabizi example buttons have declared preset requirements, covered by tests. The controller is responsible for general interpretation and material ambiguity; clarification appears only when the API requests it. Returned interpreted requirements update the editor unless the buyer has unsaved edits.

Status/phase, original provisional observation times, retailer counts and partial/error/abstention states come from the API. There are no fabricated percentages or verified labels on provisional data. API text renders as text. Polls are bounded to one at a time and stop for terminal states. Abort plus generation/run/request-revision checks discard late reads after mutations. Mutations are never automatically retried after ambiguous failures; Check latest status reconciles an existing run. A new request with an unknown start outcome may be rejected by backend admission until its existing run finishes.

Refinements retain unedited backend requirements, increment the request revision and retain previous results separately. Price, eligibility, model interpretation and verification remain backend responsibilities. Full browser-to-live-result integration remains #75, and general multilingual measurement remains later evaluation work.

## Comparison and refinement (#73)

The comparison renders up to three backend-selected model/configuration groups. A receipt from another run or revision cannot create a verified card. Additional sellers share the canonical configuration and retain their own price, condition, activation, warranty, route, timing and evidence. Selection/expanded details persist by offer ID across same-run refreshes; unavailable old selections cannot block new selections. Previous-revision cards are separately expandable and labelled historical.

Cards show configuration facts against requested requirements and factual missing terms. This is not a generated claim that one model is subjectively best: the writer's narrative integration remains separate. Evidence drawers expose per-field observation time, scope and safe source links. Cost components preserve included/additional/unknown states; estimated/incomplete totals remain separate from verified totals. Side-by-side comparison exposes canonical configuration differences and unresolved facts. Decimal-string ordering is exact, including exponent notation, and is only within the same currency; there is no browser FX conversion or eligibility recalculation.

Per-source check details display checked/failed/unchecked states only when supplied. Missing detail is explicitly unknown, not inferred from discovered inventory. Uncertain/non-qualifying offers remain outside verified slots with their reasons and missing costs. Retailer handoff uses HTTPS product links in a separate tab, with no checkout or messaging.

### Additive v2 contract fields

`ShoppingRequest.refinement_note` is optional additional buyer text. It preserves exact-model/link intent, filters, budget and original needs; recording it does not claim the controller has interpreted or applied it. `RunSnapshot.source_outcomes` defaults to empty and holds unique source IDs, display names, checked/failed/unchecked status and optional reason (required for failure). Checked means the research process checked that source; it is not a final-offer verification receipt. These optional additions keep stored v2 fixtures readable; no schema version is removed. Existing consumers may omit them. General controller population and writer integration remain full-flow work.

Run `python -m app.api_types` to regenerate both OpenAPI TypeScript types and the normalized browser fixture from the canonical `tests/fixtures/v2/workspace.json`. The fixture is synthetic; it is imported only by tests and never bundled into production. A Python regression compares the generated fixture with the current contract defaults.

Validation covers stale receipts, no padded slots, grouped sellers, side-by-side selection, retained expansion, failed/unchecked source labels, unknown/estimated costs, safe outbound URLs, and conversational refinement without weakening the original maximum. Browser QA uses intercepted synthetic API fixtures; it does not fetch retailers or establish real purchase results.
