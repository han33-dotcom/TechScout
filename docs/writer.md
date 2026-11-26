# How the baseline writes a recommendation

After verification, [the writer](../app/writer.py) builds a menu of plain-text claims from the run’s evidence. Each claim has an ID, a section and the evidence behind it. The model receives metadata and buyer priorities, then selects claim IDs in display order. Store page text, buyer free text and tool access stay outside this call.

Code discards unknown, duplicate, misplaced or excess IDs and restores required claims. Verified costs, purchase terms, material conflicts and questions relevant to the buyer remain visible. Unverified alternatives appear separately. The browser renders the result as plain text.

| Buyer sees | Rule |
| --- | --- |
| Seller comparison | Compare verified totals in one currency and disclose differences in condition, fulfillment and payment. |
| Cost breakdown | Show included charges once. Name unresolved mandatory fees. Treat the maximum budget as binding. |
| Stock and arrival | Qualify stock by branch for pickup. Label an arrival window as estimated or guaranteed according to its evidence. |
| Older or conflicting facts | Label the affected fact with its date or conflict. Keep its evidence attached. |

The writer can make one tool-free model call within the job’s final-phase reservation. With no recommendation it skips the call. Provider failure or unusable output triggers a deterministic summary. The template baseline uses that summary directly. The trace records model, prompt, token use, spend and claim counts. A mismatched request revision or a recommendation verified in another run is invalid input.

[Writer contract and worked examples](https://github.com/han33-dotcom/TechScout/blob/9006e2ac8557003c958c31f729371ccb8e160b9a/docs/writer.md) · [Buyer API](buyer-api.md) · [Shared accounting](shared-accounting.md)
