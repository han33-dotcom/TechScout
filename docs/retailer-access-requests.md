# Retailer access requests — superseded, do not send

**Status:** Superseded by the user's instruction to assume unrestricted retrieval of public website data. These drafts remain unsent and must not be sent. Retailer permission replies are not a project dependency. The text below is retained only as an archive of the discarded approach.

**Sender:** connected Gmail account (shown in the send-approval request). **Reply-To:** han33@mail.aub.edu.

## Verified recipients

- **Class Cellulars:** orders@classcellulars.com — [official contact page](https://classcellulars.com/contact/)
- **Mojitech:** corporate@mojitech.net — [official contact page](https://mojitech.net/contact-us/)
- **Beytech:** info@beytech.com.lb — [official contact page](https://beytech.com.lb/contact-us/)

Mojitech's corporate address is selected from its official contact page; Class's published orders address is asked to direct the request to its access approver. No recipient was guessed.

## Exact draft example

**Subject:** AUB student project: permission for read-only smartphone offer data

Hello Class Cellulars team,

My name is Hassan Nasrallah. My teammates and I are AUB students developing Tech Scout for our EECE503P course: a noncommercial prototype that compares smartphone offers available in Lebanon.

Could you approve read-only access to a small selection of your smartphone offers through an approved API/CSV/JSON feed, or, if you permit it, your public product pages?

We propose collecting only factual model/variant and seller identifiers, prices/currency/VAT status, published stock, warranty and delivery information. We would keep timestamped factual observations for up to 30 days for course evaluation and show those facts with links to the original offers.

For page access, we propose an identified client, at most six requests per minute, one concurrent request and four requests per shopping run. We would follow any stricter limits you set. We would not access accounts, carts or checkout, copy images or long descriptions, or continue after permission is withdrawn.

Please confirm whether this is permitted and which endpoints, fields, rate limits and retention conditions apply. Any later commercial or public deployment would require a separate agreement. If another person handles access approval, please direct this request to them.

Thank you,
Hassan Nasrallah
Tech Scout student team
han33@mail.aub.edu

The other two drafts have the same body with the retailer greeting changed.

## Technical findings behind the shortlist

- Class: public product variants and WooCommerce Store API were previously reachable; four 256 GB color variants at a captured USD 1,100. Delivery evidence conflicts and current reuse terms remain unresolved.
- Mojitech: current product HTML contains twelve variants; only the Blue/White ABM-warranty variants have positive prices and are purchasable. Its Store API declares one fractional digit, so 11000 means USD 1,100; do not assume two digits. SIM/region mapping and delivery/tax details require clarification.
- Beytech: a new ninth candidate. Current HTML and Store API expose four 256 GB color variants at a captured USD 1,079. The API declares two fractional digits; stock/purchasability flags are present. Published privacy/warranty pages and robots rules do not establish the project reuse scope.

These are captured technical observations, not valid final offers, permission grants or proof that an API is licensed for our application. Both Mojitech and Beytech return parent and variation records; deduplicate by scoped variant identity. PC and Parts was checked as an alternative, but no validated overlap was established and it is not admitted.

## Closing the access issue

1. Obtain an explicit permitted-use basis for three sources; a scoped affirmative reply or agreed feed can supply one. No reply or an HTTP success is not permission.
2. Verify the authorizer, exact allowed endpoints/fields, any credential handling, retention and rate/concurrency/run limits, and the noncommercial/public-use boundary.
3. Bind exact variants and required payable-cost/fulfillment facts to evidence; preserve unresolved fields.
4. Only then promote each reviewed source in the registry and evaluate useful overlapping coverage. The future HTTP gate must enforce source rules on every retrieval.

The draft IDs and Gmail metadata are retained in the local execution session; no send action has been taken. This file contains the complete reviewable message and intended recipients.
