# Grounded writer and deterministic fallback

`app.writer` implements the `Writer` port (`app/contract_interfaces.py`) for issue #71.
After final verification, one writing step explains the checked results: why each
option fits, how its sellers differ, what each verified total contains, and what is
still open. The writer never researches. It has no gate, store, tracer or research
handle, and its single provider call carries no tools.

## How a claim reaches the buyer

1. **Claims are built in code.** `build_menu(context)` turns the validated v2 records
   in a `WritingContext` into claims. A claim is a sentence rendered from the records,
   bound to the evidence IDs behind it. The renderer uses:
   - exact Decimal money;
   - checked times in UTC;
   - known, unknown and conflict states;
   - the route, condition, warranty and arrival basis.
2. **The model sees metadata only.** The model sees each claim's ID, section, kind,
   group and priority tags, plus the buyer's category, search type, priorities and
   whether a budget target or maximum exists. It never sees claim text, store names,
   page excerpts, URLs, raw IDs or the buyer's free text. So nothing a product page
   says can reach it as an instruction.
3. **The model returns claim IDs.** For each section (`fit`, `seller`, `cost`,
   `open`, `unverified`), it lists the claims that help this buyer decide, most
   relevant first. The prompt is `writer-v1`, recorded with every call.
4. **Code validates the reply.** It discards:
   - an unknown ID (`unknown_claim`);
   - a repeated ID (`duplicate`);
   - an ID placed in another section (`wrong_section`);
   - any ID beyond 40 (`over_limit`).

   The model cannot add a number, name, comparison or claim of its own. The counts of
   discarded IDs are traced.
5. **Code composes the text.** The kept claims come first, in the model's order. Every
   required claim the model left out is then added. Required claims cover:
   - each option's specifications;
   - each seller's route, purchase terms and check time;
   - each verified total and its budget relation;
   - open priorities, software and coverage;
   - every unverified alternative.

   Optional claims the model leaves out are omitted: side-by-side differences, seller
   comparisons, warranty and arrival lines, and unpublished optional facts. Two
   exceptions apply:
   - warranty and arrival lines, and open questions about warranty or delivery terms,
     become required when the buyer lists warranty or delivery as a priority;
   - an open question that names a conflicting fact is always required.

The explanation is plain text under fixed headings:
- "Why these options fit"
- "Seller choice"
- "Costs"
- "Open questions"
- "Not verified, shown separately"

`Explanation.evidence_ids` lists the evidence behind the verified claims shown, and
never the evidence of unverified alternatives. Render the text as text, never as HTML
or Markdown.

## Wording rules

- **Comparisons are scoped.** For example: "Of the 2 verified totals checked in this
  run for option 1, Book-Local has the lowest (USD 930)."
  - Equal totals are called equal.
  - Sellers in different currencies are not ranked.
  - When grouped sellers differ in pickup or delivery, condition, activation or
    payment, the comparison says so ("these offers differ in purchase terms: ...").
  - Product differences are listed side by side without ranking.
  - Nothing is called the cheapest or the best.
- **A cost breakdown is a sum only when exact arithmetic in one currency confirms it.**
  The cost rules have already decided each conditional charge, and the writer places
  it by that arithmetic, never by re-deciding the condition:
  - it is in the sum when the total includes it ("applies to this order under its
    listed condition");
  - it is "not part of this total" when the total equals the sum without it;
  - otherwise it is "whether it is in this total could not be confirmed", and the total
    is "as computed by the cost rules".

  A pickup total says that travel to the branch is not included.
- **Budget wording follows the buyer's limits.**
  - A budget target is a preference and the maximum is a hard limit.
  - A total in another currency without a checked conversion is not compared.
  - A converted quote is an upper bound: "at most", or "possibly above" the target. The
    conversion's rate evidence is cited when the caller passes that record in
    `WritingContext.evidence`; otherwise the text says "rate evidence not attached".
- **Purchase terms are stated per seller.** Each seller line gives the route,
  condition, activation and payment terms where the offer has them. Branch stock is
  worded only for pickup, and stock is never called reserved.
- **Arrival is stated only when checked in this run and not yet past.** The dates are
  Lebanese calendar dates: the earliest day uses UTC+2 and the latest UTC+3, Lebanon's two
  offsets, so a window is never narrowed or reversed. The line says whether the dates are an
  estimate or guaranteed. A stored or past window, or a
  conflicting one, becomes an open question.
- **Unknown stays unknown.** Missing costs of unverified alternatives are named and
  "not counted as zero".
  - A buyer priority with no checked evidence is reported: battery life and
    portability have no field in any category.
  - Software compatibility is reported as not checked.
- **Store text is checked before it is shown.** Names, reasons and assumptions are
  replaced with a fixed label such as "seller 1 (name withheld)" when they:
  - are longer than the limit;
  - contain markup or link characters (`< > { } [ ] * _ # | \` and backtick), a URL or
    `www.`;
  - contain marketing words ("cheapest", "best", "guaranteed", ...).

  Parentheses are allowed, so names like "iPad Pro 11-inch (M4)" are shown.
- **Older evidence is labelled per fact.** A specification, comparison value or
  warranty observed before this run is marked "stored record, observed <date>" on that
  fact, not on its neighbours.
- **Conflicts are reported as conflicts.** For example, "warranty length: conflicting
  values reported". They are never shown as "not published", and the open question cites
  the conflicting records (and a stored arrival's record).

## Fallback

Every failure gives the deterministic summary, which contains every claim in menu
order and is built from the same records. Failures include:
- any provider status other than `completed`: timeout, budget denial, refusal,
  invalid output, cancellation, HTTP or transport error, an unpriced model, or input
  that is too large;
- an exception from the provider;
- a prompt version not in `PROMPTS`;
- a reply with no valid ID;
- an error inside the writer.

If even that cannot be built, a counts-only text is used. A context with no
recommendation skips the model and says "No offer was verified in this run."

A context is rejected with `ValueError`, never written up, when any of these is true:
- a recommendation was verified in another run;
- the request is not the revision the run answered;
- it holds more than three recommendations.

`TemplateWriter` is the no-model baseline: it always returns the deterministic
summary, costs nothing and needs no API key.

## Budget, timing and tracing

`writer_config()` takes the shared `model_provider` settings with
`max_input_tokens` 32768 and prompt version `writer-v1`. One call therefore reserves
USD 0.2048 at the configured rates, within the USD 0.25 verification reserve.

Full-product roles call `OpenAIWriter.for_job(job, seconds=s)` after
`job.enter_final_phase()`. The ledger lease and the provider timeout are both `s`. The
writer's own lease is the full `s`; do not reuse the planner's spend view, whose lease
is the planner's call time.

`OpenAIWriter.explain()` returns a `WriterResult`. The caller records it with
`tracer.stage('write', data=result.trace_data(), spend=result.spend)`. `trace_data()`
holds identifiers and counters only: source (`model`, `mixed` or `fallback`), reason,
provider status, prompt version, model, tokens, kept, added and discarded counts. An
unknown cost keeps the reservation as its upper bound.

## Integration and limits

Since #75 the buyer controller (`app.buyer_flow`, see [the first live flow](live-flow.md))
builds the `WritingContext` after final verification. It calls
`OpenAIWriter.for_job` with an OpenRouter provider that keeps `writer-v1` and the
writer's own token allowance. It publishes the result as the additive
`RunSnapshot.explanation` field, which the browser shows as plain text. With no
verified offer, the writer makes no model call and the deterministic summary is
published.

The live v1 controller produces v1 recommendations, so today the writer is
demonstrated with the v2 fixtures and offline tests only. No live model call has been
made.

Suitability claims ("this laptop runs CAD", "this battery lasts all day") are not made:
no checked field supports them. Evidence-backed suitability findings from the
orchestrator or evaluator can be added to `WritingContext` later as another defaulted
field.
