"""Final live verification (issue #70, T34): what the run controller decides about its finalists.

In the final phase the run controller (app.runs) re-reads the finalists' products through the
gate, best first, within the verification reserve and the store's per-run cap. It re-assesses the re-read
offers with the unchanged fixed rules, then asks this module three questions. Nothing here does
I/O, so each rule can be tested on its own:

- next_target: which finalist to re-check next;
- left_unchecked: which finalists could have been shown but were not re-checked;
- recheck_problem: whether a re-read offer may stand.

The refresh boundary is reply membership, never a timestamp. An offer's decisive facts (price,
purchasability, online stock and branch stock) must cite replies the gate returned in the final
phase. A copy a reader cached, an earlier read in this run or an index entry never qualifies,
whatever time it carries. Every other record must come from that re-read too, or from a store
policy page read in this run that the shared freshness policy (app.freshness, T11) still allows
reusing.
"""
from collections.abc import Collection, Iterator, Sequence
from datetime import datetime

from app import contracts_v2 as v2
from app.assessment import Assessment
from app.contracts import Evidence, Offer
from app.freshness import FreshnessContext, FreshnessPolicy, assess_evidence

Reply = tuple[str, datetime]  # (response_sha256, observed_at) of one gate reply
Pair = tuple[Offer, Assessment]

# recheck_problem outcomes
STALE = 'stale'  # a record that no reply of this run backs
NOT_CURRENT = 'not_current'  # this run's evidence, but not from the final re-read or a reusable policy page

# Labels the transient v2 view of a v1 policy record; it is never stored or indexed.
POLICY_VIEW_CORPUS = 'v1-run-policy-view'


def rank(pairs: Sequence[Pair]) -> list[Pair]:
    """The shortlist order (app.runs._shortlist): cheapest verified total first, then offer ID."""
    return sorted(pairs, key=lambda pair: (pair[1].cost.verified_total.amount, pair[0].offer_id))


def next_target(ranked: Sequence[Pair], confirmed: Collection[str], settled: Collection[str],
                limit: int) -> Offer | None:
    """The best-ranked finalist not yet settled (re-read, or skipped for its store's cap), while
    fewer than `limit` confirmed finalists rank above it; None once the shortlist is decided."""
    above = 0
    for offer, _ in ranked:
        if above >= limit:
            return None
        if offer.offer_id in confirmed:
            above += 1
        elif offer.offer_id not in settled:
            return offer
    return None


def left_unchecked(ranked: Sequence[Pair], confirmed: Collection[str], limit: int) -> list[str]:
    """Finalists that are not confirmed and have fewer than `limit` confirmed finalists ranked
    above them. These could have been shown, so they are listed as not verified. Lower ones stay
    below the shortlist, unlisted, as before."""
    above, ids = 0, []
    for offer, _ in ranked:
        if offer.offer_id in confirmed:
            above += 1
        elif above < limit:
            ids.append(offer.offer_id)
    return ids


def decisive_evidence_ids(offer: Offer) -> set[str]:
    """Evidence behind the facts that change by the minute: price, purchasability, online stock and
    each branch's stock. These must come from the final re-read itself."""
    facts = [offer.price, offer.purchasable, offer.online_stock, *(b.stock for b in offer.pickup_stock)]
    return {evidence_id for fact in facts for evidence_id in fact.evidence_ids}


def _reply(record: Evidence) -> Reply:
    return record.response_sha256, record.observed_at


def policy_view(record: Evidence) -> v2.Evidence:
    """A v1 store-policy record as the shared freshness policy reads it: a runtime observation of
    'policy.<field>' with no run reference, so only the policy reuse window applies to it."""
    data = record.model_dump()
    data.update(schema_version=2, field_path=f'policy.{record.field_path}', origin='runtime',
                corpus_version=POLICY_VIEW_CORPUS, run=None)
    return v2.Evidence.model_validate(data)


def reusable_policy(record: Evidence, offer: Offer, now: datetime,
                    policy: FreshnessPolicy = FreshnessPolicy()) -> bool:
    """Whether the shared freshness policy still allows reusing a store-wide policy record for
    this offer. A record from a later wall-clock reading than `now` (a clock step) is judged at
    its own time, never as an observation from the future."""
    context = FreshnessContext(now=max(now, record.observed_at), product_id=offer.variant.product_id,
                               variant_id=offer.variant.variant_id)
    return assess_evidence(policy_view(record), context, policy=policy).reusable


def _records(offer: Offer) -> Iterator[tuple[Evidence, bool]]:
    decisive = decisive_evidence_ids(offer)
    for record in offer.evidence:
        yield record, record.evidence_id in decisive


def recheck_problem(offer: Offer, *, run_replies: Collection[Reply], final_replies: Collection[Reply],
                    policy_replies: Collection[Reply], now: datetime,
                    policy: FreshnessPolicy = FreshnessPolicy()) -> str | None:
    """None when a re-read offer may stand on its evidence.

    STALE: a record cites a reply this run never received, such as a reader's cached copy.
    NOT_CURRENT: a decisive record is not from the final re-read, or another record is neither from
    the final re-read nor from a store policy page read in this run that may still be reused.
    """
    if any(_reply(record) not in run_replies for record in offer.evidence):
        return STALE
    for record, decisive in _records(offer):
        if _reply(record) in final_replies:
            continue
        if decisive or _reply(record) not in policy_replies:
            return NOT_CURRENT
        if not reusable_policy(record, offer, now, policy):
            return NOT_CURRENT
    return None
