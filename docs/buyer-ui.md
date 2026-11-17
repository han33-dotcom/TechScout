# Buyer workspace

The React/TypeScript/Vite buyer interface uses the generated v2 API types and same-origin BuyerApi. Start with `cd frontend`, `npm ci`, and `npm run dev`. Vite proxies `/api` to `http://127.0.0.1:8000` (override with TECHSCOUT_API_ORIGIN). Use the backend setup in buyer-api.md. A worker/controller is required for real shopping progress; a missing worker leaves a request honestly queued.

`npm run typecheck`, `npm test`, and `npm run build` run strict TypeScript checks, offline component/race tests, and the production build. CI runs these alongside the existing Python/PostgreSQL/Docker checks. FastAPI serves frontend/dist in the production stack. No provider keys, browser provider calls, external fonts or tracking scripts are used.

## Request and progress (#72)

Needs, exact-model and HTTPS product-link entry share editable five-category requirements. Lebanon/new condition and international-off are defaults. Target budgets remain preferences and a separate field sets an explicit maximum. Original text is retained; the frontend does not claim to understand arbitrary natural language. English, Arabic and Arabizi example buttons have declared preset requirements, covered by tests. The controller is responsible for general interpretation and material ambiguity; clarification appears only when the API requests it. Returned interpreted requirements update the editor unless the buyer has unsaved edits.

Status/phase, original provisional observation times, retailer counts and partial/error/abstention states come from the API. There are no fabricated percentages or verified labels on provisional data. API text renders as text. Polls are bounded to one at a time and stop for terminal states. Abort plus generation/run/request-revision checks discard late reads after mutations. Mutations are never automatically retried after ambiguous failures; Check latest status reconciles an existing run. A new request with an unknown start outcome may be rejected by backend admission until its existing run finishes.

Refinements retain unedited backend requirements, increment the request revision and retain previous results separately. Price, eligibility, model interpretation and verification remain backend responsibilities. Full browser-to-live-result integration remains #75, and general multilingual measurement remains later evaluation work.
