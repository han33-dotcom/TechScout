# Single shopping agent development receipts

The normal buyer UI submitted two independent exact-model laptop requests to the worker. Both selected **Single shopping agent (experimental)**, new condition and delivery to Beirut. The OpenRouter model made every decision live using the frozen OpenAI route and low reasoning effort. Search responses, selected-item evidence, seller identity and fees came from synthetic development fixtures. Only the model calls incurred external traffic or spend.

| Development request | What the buyer saw | Worker time | Live calls | Settled USD |
| --- | --- | ---: | ---: | ---: |
| Aster Finch Q14, minimum 16 GB RAM | Two SerpApi fixture searches and the local catalog missed. The model switched to the Brave fixture, inspected the listing and presented one matching offer. The UI showed USD 712.35 plus USD 10 shipping, tax included, totaling **USD 722.35**. [Browser capture](recovery.png) | 30.906 s | 6 | 0.0156965 |
| Rowan Slate W8, minimum 32 GB RAM | The model inspected the selected SKU. Its current receipt showed 16 GB. After additional searches the UI explained the RAM mismatch and showed **zero verified options**. [Browser capture](wrong-variant.png) | 42.641 s | 9 | 0.0269910 |

The shared campaign ledger settled USD 0.0426875 across the two runs. Neither run left an uncertain or held charge. The [traces](traces.json) record the tool sequence, query digests, fixture receipts, selected IDs, provider metadata, time and accounting. The browser submitted the requests more than 60 seconds apart with separate sessions and the 300-second browser allowance. Worker times measure controller execution.

## Revisions and capability boundary

The tested comparison candidate is `c9c71033d7daf5508683e216f165d77c3eaab026`. Its three implementation commits descend from the [manifest's candidate starting revision](../../architecture-v2/manifest-v2.json), `85f88f48f473f49e2e3b982dab681312484efd03`. That base has the same application and frontend trees as the runnable #197 control at `34918b296c1615cba2741eb6962d5b8ba185ddad`. Both use `app.buyer_flow:run`. The candidate defaults to the control and accepts the opt-in agent selector.

The release PR also integrates with main's subsequent seller-rule change (#205). The frozen-base candidate remains in its Git ancestry so #201 can build the comparison without giving the agent a one-sided seller-rule change. The traces record the served candidate asset hashes. Scoring requires a separate paired run lock for both effective revisions and their shared adapter.

These runs exercised search-channel choice, recovery, inspection, selected-variant rejection, exact fee arithmetic and buyer-visible publication. Offline integration tests also exercised clarification and feedback, an unknown fee, and invalidating an earlier verification after a failed state-changing browser action. Live search providers, managed scrape, a live retailer browser selection, branch inventory and pre-order quotes require #201 preflight. No frozen request, grader reference or captured answer was used for development. The examples exercise parts of buyer scenarios 04, 05, 08 and 18. Product acceptance and scoring remain open.

## Reproduce on development inputs

From the comparison revision, install the repository requirements, install frontend dependencies and build the frontend. With the backend OpenRouter key and the shared campaign ledger configured, run:

```text
python acceptance/development/shopping-agent/serve.py --live-model --traces var/shopping-agent-development
```

Open the displayed loopback port (8790 by default) in two fresh browser sessions. Choose the agent, **An exact model**, **Laptops**, the model and RAM from each row, and delivery to Beirut. Submit at least 60 seconds apart. The development server starts a temporary migrated product database. Its fixture search bank is independent of action order. For the recovery model the SerpApi fixture misses and the Brave fixture returns the listing. A live model may choose a different sequence.

Earlier release-base setup runs used two fixture banks. One returned a retired link which the model skipped before finding the correct listing. A second relied on a particular query word and led to a clarification followed by abstention. Both also rejected the wrong-memory case. Those four development requests settled USD 0.0481575 in total, including the clarification continuation. They are separate from the two frozen-base demonstrations above.
