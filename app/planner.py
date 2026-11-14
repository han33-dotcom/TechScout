"""Evidence planner (MVP 12): chooses the next evidence action, or stops.

The planner owns no decision that matters for the buyer: eligibility, arithmetic, source
permissions and budgets stay in code. It picks one action from a menu the run controller built,
and sees only normalized constraints, gap field paths with fixed reasons, and counters, never
seller text.
"""
from typing import Literal, Protocol

from pydantic import Field, JsonValue

from app.assessment import EvidenceGap
from app.contracts import Count, Model, Money, PositiveInt, Text

Tool = Literal['search_candidates', 'fetch_offer', 'fetch_policy']


class PlannerAction(Model):
    action_id: Text  # 'A1', 'A2', ... stable within one turn
    tool: Tool
    source_id: Text
    target: Text | None = None  # the offer ID or policy ID the tool takes
    settles: list[Text] = Field(default_factory=list)  # gap IDs this action may settle
    calls: PositiveInt  # gate requests it needs, counted conservatively


class PlannerState(Model):
    request: dict[str, JsonValue]  # normalized buyer constraints only
    gaps: list[EvidenceGap]
    actions: list[PlannerAction]
    turn: Count
    max_turns: PositiveInt
    remaining: dict[str, JsonValue] = Field(default_factory=dict)
    history: list[dict[str, JsonValue]] = Field(default_factory=list)  # fixed-vocabulary outcomes only


class PlannerUsage(Model):
    model: Text
    input_tokens: Count
    output_tokens: Count
    cost: Money


class PlannerDecision(Model):
    kind: Literal['act', 'stop', 'invalid']
    action_id: Text | None = None
    reason: Text
    usage: PlannerUsage | None = None


class Planner(Protocol):
    name: str
    config: dict

    def decide(self, state: PlannerState, budget) -> PlannerDecision: ...


class RulePlanner:
    """Deterministic baseline: the action that may settle the most open gaps, else stop."""
    name = 'rule-planner'
    config = {'provider': 'none', 'model': 'rule-planner'}

    def decide(self, state: PlannerState, budget=None) -> PlannerDecision:
        open_gaps = {g.gap_id for g in state.gaps}
        best = max(state.actions, key=lambda a: (len(open_gaps & set(a.settles)), -_order(a.action_id)),
                   default=None)
        if best is None or not open_gaps & set(best.settles):
            return PlannerDecision(kind='stop', reason='no action can settle an open gap')
        return PlannerDecision(kind='act', action_id=best.action_id, reason='settles the most open gaps')


def _order(action_id: str) -> int:
    digits = action_id.lstrip('A')
    return int(digits) if digits.isdigit() else 1 << 30
