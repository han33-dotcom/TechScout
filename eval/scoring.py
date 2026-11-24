"""Score independently adjudicated runs according to eval/protocol.json."""
import argparse
from collections import Counter, defaultdict
from decimal import Decimal
import json
from math import ceil
from pathlib import Path
from statistics import mean
from typing import Literal

from pydantic import Field, model_validator

from app.contracts import Amount, Model, Text
from eval.scope import check_slot, study_plan, summary


class Verdict(Model):
    offer_id: Text
    verdict: Literal['valid','invalid','unverifiable']


class Trial(Model):
    case_id: Text
    side: Literal['full-warm','single-agent-warm','fixed-heuristic-warm','full-cold','no-investigation']
    repetition: int = Field(ge=1, le=3)
    run_id: Text
    outcome: Literal['recommended','abstained','partial','clarification_unresolved','error','timeout','cancelled']
    adjudicated_class: Literal['answerable','no_match_observed']
    selected: tuple[Verdict,...] = Field(max_length=3)
    stated_blocking_reason: str | None
    constraints_preserved: bool
    timely: bool
    invented_offer: bool
    # Null observations are censored, including timeouts.
    first_useful_seconds: Amount | None
    verified_seconds: Amount | None
    spend_usd: Amount
    # References to durable run traces, corpus versions and ledger receipts.
    trace_id: Text
    corpus_version: Text
    ledger_reference: Text
    needs_fit: Literal['supported','unsupported','unassessed'] | None

    @model_validator(mode='after')
    def coherent(self):
        if len({o.offer_id for o in self.selected}) != len(self.selected):
            raise ValueError('Duplicate selected offer')
        if self.outcome=='recommended' and not self.selected or self.outcome=='abstained' and self.selected:
            raise ValueError('Selected offers disagree with the recorded outcome')
        if self.adjudicated_class=='no_match_observed' and any(v.verdict=='valid' for v in self.selected):
            raise ValueError('A valid offer establishes that this case is answerable')
        return self

    def success(self):
        if (not self.timely or not self.constraints_preserved or self.invented_offer
                or self.outcome in {'error','timeout','cancelled','clarification_unresolved'}):
            return False
        if self.adjudicated_class=='answerable':
            return bool(self.selected) and all(v.verdict=='valid' for v in self.selected)
        return not self.selected and bool((self.stated_blocking_reason or '').strip())


def percentiles(times):
    observed = sorted(t for t in times if t is not None)
    def quantile(q):
        index = ceil(q*len(times))-1
        return str(observed[index]) if 0 <= index < len(observed) else None
    return {'p50':quantile(.50),'p95':quantile(.95),'observed':len(observed),'total':len(times)}


def score(trials, *, acquisition_usd, maintenance_usd):
    grouped = defaultdict(list)
    seen = set()
    for r in trials:
        identity = (r.case_id,r.side,r.repetition)
        if identity in seen:
            raise ValueError('Duplicate case, side and repetition')
        seen.add(identity)
        grouped[r.side].append(r)
    heldout_ids = {case_id for case_id, _, _ in seen if case_id.startswith('H')}
    if heldout_ids and any(case_id not in heldout_ids for case_id, _, _ in seen):
        raise ValueError('Score development and held-out records separately')
    scope = None
    if heldout_ids:
        plan = study_plan()
        for case_id, side, repetition in seen:
            check_slot(plan, case_id, side, repetition)
        scope = summary(plan)
        scope['missing_automated_runs'] = {
            side: sum((case_id, side, rep) not in seen for case_id, rep in plan[side])
            for side in ('full-warm', 'full-cold', 'fixed-heuristic-warm', 'single-agent-warm')}
    classes = defaultdict(set)
    for rows in grouped.values():
        for r in rows:
            classes[r.case_id].add(r.adjudicated_class)
    if any(len(values)!=1 for values in classes.values()):
        raise ValueError('A case needs one adjudicated class across all sides')
    scores = {}
    for side, rows in grouped.items():
        primary = [r for r in rows if r.repetition==1]
        offers = [v for r in primary for v in r.selected]
        cases = defaultdict(list)
        for r in rows:
            cases[r.case_id].append(r)
        def rate(fn, answerable=False):
            groups = [rr for rr in cases.values() if not answerable or rr[0].adjudicated_class=='answerable']
            return mean(mean(int(fn(r)) for r in rr) for rr in groups) if groups else None
        successes = sum(r.success() for r in rows)
        spend = sum((r.spend_usd for r in rows),Decimal(0))
        scores[side] = dict(runs=len(rows), cases=len(cases),valid_offers=sum(v.verdict=='valid' for v in offers),
            selected_offers=len(offers),valid_offer_rate=sum(v.verdict=='valid' for v in offers)/len(offers) if offers else None,
            request_success_rate=rate(Trial.success),useful_result_success_rate=rate(Trial.success,True),
            abstention_rate=rate(lambda r:not r.selected),first_useful_seconds=percentiles([r.first_useful_seconds for r in rows]),
            verified_seconds=percentiles([r.verified_seconds for r in rows if r.adjudicated_class=='answerable']),
            marginal_spend_usd=str(spend),cost_per_success_usd=str(spend/successes) if successes else None,
            needs_fit=Counter(r.needs_fit for r in primary if r.needs_fit))
    report = dict(configurations=scores,acquisition_usd=str(acquisition_usd),maintenance_usd=str(maintenance_usd))
    if scope is not None:
        report['heldout_scope'] = scope
        report['observation_method'] = 'Codex-operated retailer rechecks'
        report['unmeasured'] = ['human_shopping', 'independent_human_verification', 'human_buyer_effort']
        for item in scores.values():
            item['agent_rechecked_offer_rate'] = item['valid_offer_rate']
            item['valid_offer_rate'] = None
    return report


class PriceSide(Model):
    valid: bool
    verified_total_usd: Amount | None
    condition: Text | None
    fulfillment_basis: Text | None
    warranty: Text | None
    verification_pass: Text | None
    # Reference to the verifier's complete item/route/currency adjudication.
    evidence_reference: Text | None


def compare_price(left, right, *, market, equivalence, same_case, access_failure=False):
    if not same_case or market!='domestic':
        return 'not_comparable'
    if access_failure:
        return 'inconclusive'
    if not left.valid or not right.valid:
        return 'techscout_only_valid' if left.valid else 'baseline_only_valid' if right.valid else 'neither_valid'
    if equivalence!='confirmed_equivalent' or not left.condition or left.condition!=right.condition:
        return 'not_comparable'
    if not left.fulfillment_basis or left.fulfillment_basis!=right.fulfillment_basis:
        return 'not_comparable'
    if (not left.verification_pass or left.verification_pass!=right.verification_pass or
            any(x.verified_total_usd is None or not x.evidence_reference for x in (left,right))):
        return 'inconclusive'
    a,b = left.verified_total_usd,right.verified_total_usd
    if abs(a-b)<=max(Decimal('1'),Decimal('.005')*min(a,b)):
        return 'tie'
    return 'win' if a<b else 'loss'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trials',type=Path)
    parser.add_argument('--acquisition-usd',required=True,type=Decimal)
    parser.add_argument('--maintenance-usd',required=True,type=Decimal)
    parser.add_argument('--pairs',type=Path,help='Independent verifier pair adjudications, one JSON object per line')
    args = parser.parse_args()
    if any(not x.is_finite() or x<0 for x in (args.acquisition_usd,args.maintenance_usd)):
        parser.error('Expected nonnegative finite corpus costs')
    trials = [Trial.model_validate_json(line) for line in args.trials.read_text().splitlines() if line.strip()]
    report=score(trials,acquisition_usd=args.acquisition_usd,maintenance_usd=args.maintenance_usd)
    if args.pairs:
        pairs = []
        plan = study_plan() if 'heldout_scope' in report else None
        for line in args.pairs.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            record=json.loads(line)
            if record['case_id'].startswith('H') != (plan is not None):
                raise ValueError('Paired records must use the same study cohort as the trials')
            if plan is not None:
                check_slot(plan, record['case_id'], record['side'], 1)
                if record['market'] != 'domestic':
                    raise ValueError('A domestic held-out pair must use the domestic market')
            left,right=(PriceSide.model_validate(record[k]) for k in ('techscout','baseline'))
            outcome=compare_price(left,right,market=record['market'],equivalence=record['equivalence'],
                same_case=record['same_case'],access_failure=record.get('access_failure',False))
            pairs.append(dict(case_id=record['case_id'],side=record['side'],outcome=outcome,
                              warranties=[left.warranty,right.warranty]))
        report['paired_price_outcomes']=pairs
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
