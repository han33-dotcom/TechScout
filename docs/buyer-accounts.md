# TechScout accounts, saved locations and chat destinations

Issue [#187](https://github.com/han33-dotcom/TechScout/issues/187) adds buyer accounts to the cookie-owned chats in [buyer-api.md](buyer-api.md). It covers scenarios 12–14 of the [acceptance matrix](buyer-acceptance-matrix.md). A buyer can create a TechScout account, sign in and keep private saved locations. In a chat, the buyer can choose one location and the sellers that may receive it, or decline to give an address. Retailer pre-order quotes belong to [#186](https://github.com/han33-dotcom/TechScout/issues/186). This page ends at the hand-off to that work.

`app/buyer_accounts.py` holds the service. Migration `0010` adds five private tables: `buyer_accounts`, `buyer_signins`, `buyer_locations`, `buyer_destinations` and `buyer_attempts`. All account changes share the buyer guard transaction with runs, so a sign-in cannot race a worker write.

## Accounts and sessions

| Endpoint | Body | Result |
| --- | --- | --- |
| `GET /api/session` | none | `SessionInfo`, now with `account: {username}` or `null` |
| `POST /api/account/register` | `username`, `password` | 201 `SessionInfo`; the buyer is signed in |
| `POST /api/account/sign-in` | `username`, `password` | `SessionInfo` for a new session |
| `POST /api/account/sign-out` | `{}` | `SessionInfo` for a new anonymous session |

Every `POST` keeps the existing same-origin and `X-CSRF-Token` checks. Register and sign-in return a new CSRF token, which the browser must use from then on.

- **Usernames** are 3–32 characters: lowercase letters, digits, dots, dashes and underscores. Input is NFKC-normalized and case-folded. TechScout asks for no email or phone number.
- **Passwords** are 10–128 characters. They cannot contain the username or match a short list of common passwords. They are stored only as `scrypt` hashes (N=2¹⁴, r=8, p=1, a random 16-byte salt per password) from Python's standard library. Each hash records its parameters, and verification compares digests in constant time. Hashing runs outside database transactions.
- **Sessions rotate on sign-in.** Register and sign-in issue a new random cookie and delete the old session row, so the old cookie stops working at once. The cookie keeps its flags: HttpOnly, SameSite=Strict, and Secure under `TECHSCOUT_COOKIE_SECURE`. Signed-in sessions also expire 24 hours after creation.
- **Anonymous chats move with the browser.** On sign-in, the anonymous session's chats and address decisions move into the signed-in session. The buyer comes back to the same chat with the same run IDs, earlier revisions and offers. If the browser was signed in to a different account, nothing moves and that session is ended. Signing in again to the same account keeps the chats.
- **Sign-out ends the session.** The session's private chats and destination choices are deleted, exactly as at expiry. Saved locations belong to the account and stay. The browser receives a new anonymous session.

### Failed sign-ins and enumeration

Every failed sign-in returns the same 401 response: "The username or password is incorrect". Unknown names, malformed names, wrong passwords and empty passwords all get it. An unknown name is checked against a decoy hash, so it takes as long as a real one.

Attempts are counted before the password check, in fixed 15-minute windows. Each name allows 5 attempts, and each browser session allows 10. The per-name counter applies to names without accounts too, so a 429 does not reveal that an account exists. The table keeps SHA-256 digests rather than attempted names, and rows are removed within an hour. A successful sign-in clears that name's counter. The per-name limit lets anyone delay sign-in to a known username for up to 15 minutes; that is the trade-off for slowing password guessing without a second factor.

Registration is limited to 5 attempts per session and 60 per hour across the service, with a cap of 10,000 accounts. Registration must say when a username is taken (409); without an email channel, it cannot hide that. These limits keep that answer slow to harvest.

Validation errors on `/api/account` never echo the submitted body. A wrong type or an extra field returns only "The account request is not valid".

## Saved locations

| Endpoint | Result |
| --- | --- |
| `GET /api/account/locations` | The account's locations, by label: ID, label, city and creation time |
| `POST /api/account/locations` | `label` (1–40), `city` (2–80), `street` (3–200), optional `details` (≤200); 201 summary |
| `POST /api/account/locations/{id}/delete` | `{deleted: true}`, or 404 when the location is missing or belongs to another account |

A buyer who is not signed in gets 401 "Sign in to use saved locations". Each account can save up to 10 locations. Text is normalized, and control, bidirectional and angle-bracket characters are refused.

**The street and building details are never returned by any endpoint.** They never enter shopping requests, run snapshots, history, exports or logs. The buyer sees label and city only, and deletes and re-adds a location to change it. A location also never changes a search's requirements; the city in the request stays the buyer's own choice. Deleting a location withdraws it from every chat that selected it.

## Choosing a destination in a chat

| Endpoint | Body | Result |
| --- | --- | --- |
| `GET /api/runs/{id}/destination` | none | `DestinationState` for that run's chat |
| `POST /api/runs/{id}/destination` | `location_id`, `recipients` (1–20 retailer IDs) | Selection replaces any earlier choice |
| `POST /api/runs/{id}/destination/decline` | `{}` | Buyer keeps shopping without an address |

A chat is one request ID in one session, across all its revisions. `DestinationState` lists the Lebanese sellers in the chat's latest revision (`sellers`), whether the buyer is signed in, and the current `selection`.

- **Selecting** needs a signed-in session that owns both the chat and the location. Recipients must be sellers shown in that chat. Overseas sellers are never offered. The choice is saved for this chat only. A later chat, or a seller that appears later, needs a new choice. Saving a location and choosing its recipients stay separate actions.
- **Declining** works with or without an account. No seller receives an address. The comparison, exact product links and unknown fees stay as they were. The buyer can still select a location later.
- **Ownership:** another session or account gets 404 for the chat. It also gets 404 for another account's location, even when using its own chat.

`selection.quote_state` is `pending` after a selection and `none` after a decline. Until #186 lands, `pending` means only that the buyer has chosen. No retailer has been asked, and totals are unchanged.

## Hand-off to the pre-order quote operation (#186)

These two methods are internal Python only, never HTTP:

- `BuyerAccounts.pending_quotes(limit)` returns each chat that is waiting for quotes: its latest `RunReference` and the named retailer IDs. It returns no address.
- `BuyerAccounts.release_address(run, retailer_id)` returns the `PreciseAddress` only when that retailer is a named recipient of that run's chat and the session is alive. Otherwise it returns `None`. Its `repr()` hides the street and building details.

The following remains for #186:

- request the quote from each named retailer only;
- move `quote_state` through its later states (for example requested, quoted or unavailable);
- publish revised totals and advice in the same chat without a new search.

The selection, the recipients and this hand-off are ready for it.

## Validation

`tests/test_buyer_accounts.py` covers the following, using only the synthetic accounts and invented addresses in `tests/fixtures/accounts.json` and the synthetic v2 workspace:

- hash format, salting and verification;
- registration rules, without echoing secrets;
- session rotation, anonymous-chat adoption and stale-cookie refusal;
- identical sign-in failures;
- per-name, per-session and registration throttles;
- sign-out and expiry clean-up;
- CSRF and origin checks on every new mutation;
- location bounds and masking;
- cross-account isolation for locations and chats;
- recipient validation and release;
- the selection carrying to the next revision;
- decline;
- withdrawal on deletion;
- reuse in a later session;
- the absence of the street from logs, snapshots, history, exports and API responses.

The existing migration test compares migration `0010` with the models on SQLite and PostgreSQL.
