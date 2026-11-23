# Private session history and exports

Recent searches reuse the private `buyer_sessions` / `buyer_runs` tables. They are never indexed into the shared evidence corpus. `GET /api/history` returns at most 20 searches (latest revision of each request, most recently updated first); `limit` accepts 1–20. The list exposes only category, revision, status, time and the owned run reference. `GET /api/history/{run_id}` reopens that exact revision with `historical: true`, including its previous revision where present. All reads require the same unexpired, HttpOnly browser session cookie; another session receives 404 for a guessed run ID. Responses are not cached.

The UI loads history on demand and labels reopened comparisons historical, including their verification badges. Recheck these requirements starts a new request/run with revision 1 with the saved product and fulfillment requirements in the domestic market. It does not reuse an old verification receipt as current evidence. Existing admission rules reject starting another run while this session has an active search. Reopening a saved snapshot does not resume its worker or poll it automatically.

## Expiry and deletion

Sessions have a fixed 24-hour lifetime from creation; viewing history does not extend it. Access is denied immediately at expiry. Expired sessions and their cascading run rows are physically removed by the next session-creation cleanup or worker/service recovery. This is opportunistic cleanup, not a scheduled deletion SLA. Operators must separately configure backup retention; deletion does not rewrite backups or remove downloaded reports.

`POST /api/history/{run_id}/delete` requires the existing same-origin and CSRF checks. It deletes every revision of the owned request. If any revision is active, it returns 409: cancel that search first. Deletion shares the worker state-transition transaction guard; late worker writes cannot resurrect a deleted run. The UI asks for confirmation, clears the selected history and matching current workspace after success, and never retries a mutation automatically. Public source documents and other sessions are unaffected.

## Shareable export

`GET /api/history/{run_id}/export?include_evidence=true` downloads `techscout-comparison.txt` as `text/plain`, `nosniff`, and `no-store`. The optional evidence switch omits field/source rows when false. The server builds an allowlisted summary of the chosen comparison: product configuration, seller, terms, route kind, arrival window, exact costs and optional field evidence dates/source links. Uncertain alternatives stay separate; missing costs stay unknown. Every export is explicitly historical.

The report omits session/run/request/offer/evidence identifiers, request text, buyer preferences and destination details, provider credentials, raw excerpts, and free-text exclusion reasons. Product/source URLs allow HTTPS without embedded credentials and drop query strings/fragments, so signed links may need to be opened from the private workspace instead. Retailer text is reduced to bounded plain text with markup, script/style contents and control characters removed. Only report fields are exported; this is not an export of private database rows. A downloaded report is a local file that its owner may deliberately share.

Validation uses offline fixtures: owned/cross-session reads and mutations, history revisions and limits, expiry/cleanup, whole-request deletion, export sanitization and missing costs, plus UI reopen/recheck/delete/export interactions. Browser checks use intercepted synthetic API responses. They do not establish live retailer coverage or end-to-end recommendations; that integration remains #75.
