"""Offer assessments shared by eligibility (MVP 08), costs (MVP 09), runs (MVP 11) and the planner.

Deterministic rules make every decision; the planner only sees gaps, never seller text.
Reasons use fixed wording plus normalized values, so they are safe to show the planner.
"""
from typing import Literal

from pydantic import Field, StrictBool, model_validator

from app.contracts import CostComponent, Model, Money, Text
from app import contracts_v2 as v2

Outcome = Literal['pass', 'fail', 'unresolved']
Decision = Literal['recommendable', 'unresolved', 'rejected']


class FullProductAssessment(Model):
    """Decision over held evidence; this is never a current-run verification."""
    offer_id: Text
    eligibility: 'Eligibility'
    cost: v2.Cost
    within_maximum: StrictBool | None

    @model_validator(mode='after')
    def same_offer(self):
        if self.offer_id != self.eligibility.offer_id:
            raise ValueError('Assessment must cover the eligibility offer')
        return self

    @property
    def decision(self) -> Decision:
        if self.eligibility.outcome=='fail' or self.within_maximum is False:
            return 'rejected'
        if self.eligibility.outcome=='pass' and self.cost.status=='verified' and self.within_maximum is True:
            return 'recommendable'
        return 'unresolved'


def assess_full_product(request: v2.ShoppingRequest, offer: v2.Offer, *, fx=(), now=None, estimates=None):
    from app.costs import calculate_domestic_cost
    from app.eligibility import check_full_product
    eligibility = check_full_product(request,offer)
    cost = calculate_domestic_cost(request,offer,fx=fx,now=now,estimates=estimates)
    within = None
    if cost.status=='verified':
        maximum = request.budget.maximum if request.budget else None
        total = cost.budget_conversion.quote if cost.budget_conversion else cost.total
        within = maximum is None or total.currency==maximum.currency and total.amount<=maximum.amount
    return FullProductAssessment(offer_id=offer.offer_id,eligibility=eligibility,cost=cost,within_maximum=within)


class EvidenceGap(Model):
    """A required fact the rules could not settle, which more evidence might settle."""
    offer_id: Text
    source_id: Text
    field_path: Text  # an Offer field path, as in Evidence.field_path
    reason: Text

    @property
    def gap_id(self) -> str:
        return f'{self.offer_id}#{self.field_path}'


class Check(Model):
    name: Text
    outcome: Outcome
    reason: Text
    field_paths: list[Text] = Field(default_factory=list)
    evidence_ids: list[Text] = Field(default_factory=list)


def combine(outcomes) -> Outcome:
    """Any failure fails; otherwise anything unresolved stays unresolved."""
    outcomes = set(outcomes)
    return 'fail' if 'fail' in outcomes else 'unresolved' if 'unresolved' in outcomes else 'pass'


class Eligibility(Model):
    offer_id: Text
    outcome: Outcome
    checks: list[Check] = Field(min_length=1)
    gaps: list[EvidenceGap] = Field(default_factory=list)

    @model_validator(mode='after')
    def consistent(self):
        if self.outcome != combine(c.outcome for c in self.checks):
            raise ValueError('Eligibility outcome must follow its checks')
        if self.outcome == 'pass' and self.gaps:
            raise ValueError('A passing offer has no evidence gaps')
        if any(g.offer_id != self.offer_id for g in self.gaps):
            raise ValueError('Gaps must belong to the assessed offer')
        return self


class CostResult(Model):
    offer_id: Text
    status: Literal['verified', 'unresolved', 'excluded']
    basis: Text  # what the total covers, e.g. 'delivery to Beirut' or 'pickup; travel excluded'
    components: list[CostComponent] = Field(default_factory=list)
    verified_total: Money | None = None
    known_subtotal: Money | None = None  # a lower bound when unresolved, never shown as a total
    within_budget: StrictBool | None = None
    reasons: list[Text] = Field(default_factory=list)
    gaps: list[EvidenceGap] = Field(default_factory=list)

    @model_validator(mode='after')
    def truthful_total(self):
        if self.status == 'verified':
            if self.verified_total is None or self.within_budget is None or self.gaps:
                raise ValueError('A verified cost needs a total, a budget check and no gaps')
            if any(c.state == 'unknown' for c in self.components):
                raise ValueError('Unknown components cannot produce a verified total')
        elif self.verified_total is not None or self.within_budget is not None:
            raise ValueError('Only verified costs carry a total and a budget check')
        elif not (self.reasons or self.gaps):
            raise ValueError('Unverified costs need a reason or a gap')
        if any(g.offer_id != self.offer_id for g in self.gaps):
            raise ValueError('Gaps must belong to the assessed offer')
        return self


class Assessment(Model):
    """One offer's eligibility and cost; only both together decide recommendability."""
    offer_id: Text
    eligibility: Eligibility
    cost: CostResult

    @model_validator(mode='after')
    def same_offer(self):
        if not self.offer_id == self.eligibility.offer_id == self.cost.offer_id:
            raise ValueError('An assessment covers one offer')
        return self

    @property
    def decision(self) -> Decision:
        if (self.eligibility.outcome == 'fail' or self.cost.status == 'excluded'
                or (self.cost.status == 'verified' and not self.cost.within_budget)):
            return 'rejected'
        if self.eligibility.outcome == 'pass' and self.cost.status == 'verified':
            return 'recommendable'
        return 'unresolved'

    @property
    def gaps(self) -> list[EvidenceGap]:
        seen, gaps = set(), []
        for gap in [*self.eligibility.gaps, *self.cost.gaps]:
            if gap.gap_id not in seen:
                seen.add(gap.gap_id)
                gaps.append(gap)
        return gaps

    def reasons(self) -> list[str]:
        """Fail reasons for a rejected offer, open doubts for an unresolved one."""
        if self.decision == 'rejected':
            reasons = [c.reason for c in self.eligibility.checks if c.outcome == 'fail']
            if self.cost.status == 'excluded':
                reasons += self.cost.reasons
            elif self.cost.status == 'verified' and not self.cost.within_budget:
                total = self.cost.verified_total
                reasons.append(f'total {total.amount} {total.currency} is over the budget')
        else:
            reasons = [c.reason for c in self.eligibility.checks if c.outcome == 'unresolved']
            if self.cost.status == 'unresolved':
                reasons += self.cost.reasons
        # Eligibility and costs can state one doubt alike ('price is unknown'); it is listed once.
        return list(dict.fromkeys(reasons)) or [f'offer is {self.decision}']
