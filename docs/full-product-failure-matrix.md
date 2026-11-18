# Full-product failure matrix and threat model

The test suite treats buyer requests and session data as private. Retailer pages,
discovery links, research findings, and model replies are untrusted inputs.
The source gate authorizes outbound reads. The controller and durable ledger
authorize work. The buyer API owns session and run transitions. The typed result,
cost engine and verifier govern what can become a recommendation.

| Failure or threat | Expected behavior exercised by offline fixtures | Evidence |
| --- | --- | --- |
| Wrong model, variant or pickup branch | A mismatch is rejected even with current price and stock. Variant evidence and branch stock stay bound to the listing and selected branch. | [Owned publication](../tests/test_buyer_api.py), [variant and branch binding](../tests/test_contracts_v2.py), [eligibility](../tests/test_full_product_assessment.py) |
| Missing, conditional or doubled mandatory fee | Unknown fees prevent verification. Included tax stays in the price. The API rejects an unresolved or inconsistent domestic total even when the offer is otherwise eligible. | [Cost assessment](../tests/test_full_product_assessment.py), [conditional fees at publication](../tests/test_buyer_api.py) |
| Quote applied to another route, package or validity window | A receipt for a different route does not support the selected offer. A change in package invalidates quote reuse. At the expiry instant the quote cannot complete a payable total. | [Quote reuse](../tests/test_freshness.py), [quote publication](../tests/test_buyer_api.py) |
| Cached price, stale stock, unsupported or contradictory claims | Historical offers remain provisional. A decisive stored receipt cannot stand in for this run's verification. Critique withholds unsupported and conflicting specifications even if its provider fails. | [Final recheck](../tests/test_verification.py), [publication](../tests/test_buyer_api.py), [critique](../tests/test_orchestrator.py) |
| Untrusted page or URL changes tool scope | The gate refuses out-of-scope destinations before transmission. Extracted page content cannot grant tools or alter the request. | [Source gate](../tests/test_source_gate.py), [extraction](../tests/test_extraction.py) |
| Model reply attempts to add unsupported output or work | The evaluator can select only a scoped issue from the controller's menu. A second follow-up is rejected. The writer composes only from supported claims and strips unsafe store text. | [Follow-up](../tests/test_orchestrator.py), [output selection](../tests/test_writer.py) |
| Another session reads or changes a run or export | The API denies access across session boundaries and checks origin and CSRF before mutation. Published history and exports stay scoped to the owner. | [Owned API](../tests/test_buyer_api.py), [history](../tests/test_buyer_history.py) |
| Cancellation, worker restart or late completion | Cancelled critique cannot start acquisition. Owned run leases reject late publication and recover interrupted runs. | [Critique cancellation](../tests/test_orchestrator.py), [lease recovery](../tests/test_buyer_api.py) |
| Simultaneous reservations or exhausted allowance | The shared ledger meters in-flight reservations across owners. An evaluator cannot reach the model when another reservation exhausts the research allowance. | [Concurrent ledger reservations](../tests/test_accounting.py), [evaluator dispatch](../tests/test_orchestrator.py) |
| Stale vector results, private corpus data or deleted evidence | Retrieval filters by exact scope and current document. Private input is rejected by the public corpus. Deleted passages and vectors cannot reappear after a reopen, and evidence for another configuration stays available. | [Retrieval](../tests/test_retrieval.py), [retention](../tests/test_corpus.py) |

The fixtures demonstrate these outcomes with local databases, synthetic evidence,
and provider substitutes. Live source availability and the integrated browser
flow are tracked in #75 and #76. International landed-cost verification is
tracked in #63. Production limits still depend on configuration and on external
providers honoring their documented contracts.
