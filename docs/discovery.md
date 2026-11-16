# Direct Tavily discovery

`Tavily.search(request, job)` uses the [direct search endpoint](https://docs.tavily.com/documentation/api-reference/endpoint/search),
with fixed basic depth, automatic parameter selection disabled, no generated answer
or raw page content, and at most 20 leads per search. An explicit Decimal USD/credit
rate is required. The shared ledger reserves each call before dispatch; failed or
ambiguous calls retain the quote. No implicit retries run. An explicit retry is a
new billed attempt. Reported overcharges stop the campaign.

The query preserves intent, category requirements, allowed conditions, and the
distinction between a budget target and maximum. Lebanon is the default country
boost. International search is opt-in. The country parameter is only a search
ranking hint: trusted retailer market checks still apply before retrieval.

`Discovery(provider, directory, gate).discover(request, job)` selects up to three
retailers by default (configurable to 20), considering search relevance, category
coverage gaps and whether a seller is already covered. Search is open-web; it is
not restricted to the audit's priority list or a fixed 15-store universe. The
bounded results can include any newly reviewed retailer present in the directory.

New/unresolved domains, marketplaces and aggregators remain leads. Register a
reviewed canonical retailer identity/market and technical source capability before
retrieval; discovery never writes an admission grant. Selection matches canonical
directory origins; alternate hosts require their own reviewed origin record.
Current release selection, endpoint scope and capability version are checked, then
the source gate repeats admission, public-network and workload checks at dispatch.

`DiscoveryReport` retains all leads and separately tracks known retailers as
unchecked, failed or checked. A refusal before any request stays unchecked; a
failed sent request counts attempted. A successful listing retrieval is checked,
but qualifying remains zero until downstream `VerifiedOffer` validation supplies
current-run price/stock receipts bound to that source and retrieved response hash.
Snippets have no evidence IDs and cannot become purchase facts. This module does
not rank offers or claim that the returned model satisfies every buyer constraint.

Offline tests exercise 20 synthetic sellers, three selected sources, newly reviewed
retailers beyond the initial sample, unknown/aggregator leads, opt-in, exhausted
budget, mid-run revocation, provider failures, overcharges and response bounds.
No production search has been run; provider credentials/rates and end-to-end role
wiring are explicit operational/integration configuration.
