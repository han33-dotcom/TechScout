# Automatic retailer onboarding

A background worker in [app/onboarding.py](../app/onboarding.py) turns retailer leads into reviewed product or variant operations. A heuristic or model proposes a scope. Backend rules decide what the buyer worker can use.

1. Merge public HTTPS leads by domain and prioritize coverage gaps and buyer demand. Buyer leads retain the URL, category and market.
2. Probe up to four exact sample pages through the source gate. Validate at least two distinct listings with different response hashes. Bind price, currency and stock to the SKU and verify the stated seller and market. A variant capability requires separate SKU evidence.
3. Store the approved scope, sample receipts and version atomically. A changed scope supersedes its predecessor. Reactivation after suspension requires fresh evidence. Search, catalog, feed and sitemap scopes require separate review.
4. Recheck active capabilities. Suspend failed validation, retry temporary failures, and record missing samples when listings disappear. The background worker shares the campaign ledger and maintenance budget.

## Operate the queue

```text
python -m app.onboarding exceptions
python -m app.onboarding history --source auto-cedartech-example-7834a785
python -m app.onboarding suspend --source ID --capability product-page --reason "..."
python -m app.onboarding reopen --lead cedartech.example --note "profile fixed"
python -m app.onboarding run --max-leads 3
```

`run` makes public read-only GETs to queued shops with the heuristic proposer: no paid
calls. It needs the shared accounting ledger (`TECHSCOUT_ACCOUNTING_URL`).

Blocked leads retain their reason, URLs, hashes and extraction receipts. A temporary probe failure retries after 15 minutes and one hour. A third failure becomes a teammate exception. The shared parser requires structured listing evidence that identifies the seller. [Exact validation and retry rules](https://github.com/han33-dotcom/TechScout/blob/9006e2ac8557003c958c31f729371ccb8e160b9a/docs/retailer-onboarding.md) · [Offline scenarios](../tests/test_onboarding.py) · [Source admission](source-admission.md)
