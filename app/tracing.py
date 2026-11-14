"""Run traces (MVP 11): every tool call, planner turn and stage of a run, in order.

Each record is one Storage.record_action row carrying what that step added to the run's usage
(external calls, planner turns, spend), and one JSON line on the 'techscout.trace' logger.
Arguments hold identifiers, counters and the buyer's constraints only: page text, evidence
excerpts and other store-written values never enter a trace.
"""
from dataclasses import dataclass
from decimal import Decimal
import json
import logging
from typing import Literal

from app.contracts import ToolError, ToolResult
from app.planner import PlannerAction, PlannerDecision

TRACE_LOGGER = 'techscout.trace'
MAX_ARGUMENT_TEXT = 200  # identifiers and constraints are short; page text is not
MAX_ARGUMENTS_JSON = 4000
MAX_REASON = 300  # the planner's own short reason, kept for evaluation
Kind = Literal['tool', 'planner', 'stage']
_KINDS = ('tool', 'planner', 'stage')
_ZERO = Decimal('0')


@dataclass(frozen=True)
class TraceRecord:
    """One recorded step; the usage counts are what this step added to the run."""
    run_id: str
    seq: int
    kind: Kind
    name: str
    status: str
    duration_ms: int
    external_calls: int
    error_codes: tuple[str, ...]
    evidence_count: int
    tokens_in: int
    tokens_out: int
    spend: Decimal
    planner_turns: int = 0
    retries: int = 0
    source_id: str | None = None

    def line(self) -> str:
        """The record as one JSON log line, in a fixed shape."""
        return json.dumps({
            'run_id': self.run_id, 'seq': self.seq, 'kind': self.kind, 'name': self.name,
            'status': self.status, 'duration_ms': self.duration_ms, 'external_calls': self.external_calls,
            'retries': self.retries, 'error_codes': list(self.error_codes),
            'evidence_count': self.evidence_count, 'tokens_in': self.tokens_in, 'tokens_out': self.tokens_out,
            'spend': str(self.spend), 'source_id': self.source_id,
        }, sort_keys=True, separators=(',', ':'))


def _check_arguments(arguments) -> None:
    """Identifiers and counters only: a long string means page text slipped in."""
    def walk(value):
        if isinstance(value, str):
            if len(value) > MAX_ARGUMENT_TEXT:
                raise ValueError('Trace arguments hold identifiers and counters, never page text')
        elif isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError('Trace argument keys are strings')
                walk(key)
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)
        elif value is not None and not isinstance(value, (bool, int)):
            raise TypeError(f'Trace arguments cannot hold {type(value).__name__}')

    if not isinstance(arguments, dict):
        raise TypeError('Trace arguments are a JSON object')
    walk(arguments)
    if len(json.dumps(arguments)) > MAX_ARGUMENTS_JSON:
        raise ValueError('Trace arguments hold identifiers and counters, never page text')


def _retries(result: ToolResult) -> int:
    """Gate attempts beyond the first, from the fetch entries a store reader reports."""
    fetches = result.data.get('fetches')
    if not isinstance(fetches, list):
        return 0
    attempts = [f.get('attempts') for f in fetches if isinstance(f, dict)]
    return sum(a - 1 for a in attempts if type(a) is int and a > 1)


class Tracer:
    """Writes one run's trace, in order; summary() turns it into the run's metrics."""

    def __init__(self, storage, run_id: str, *, logger: logging.Logger | None = None):
        self.storage, self.run_id = storage, run_id
        self.records: list[TraceRecord] = []
        self.status: str | None = None
        self.stop_reason: str | None = None
        self._log = logger or logging.getLogger(TRACE_LOGGER)

    def record(self, kind: Kind, name: str, arguments: dict, result: ToolResult, *,
               source_id: str | None = None, external_calls: int = 0, planner_turns: int = 0,
               spend: Decimal = _ZERO, tokens_in: int = 0, tokens_out: int = 0) -> TraceRecord:
        """Store one step (storage assigns its seq), then log it."""
        if kind not in _KINDS:
            raise ValueError(f'Trace records are one of {_KINDS}')
        if not isinstance(result, ToolResult):
            raise TypeError('Trace records carry a ToolResult')
        _check_arguments(arguments)
        seq = self.storage.record_action(self.run_id, name, arguments, result, external_calls=external_calls,
                                         planner_turns=planner_turns, spend=spend)
        record = TraceRecord(
            run_id=self.run_id, seq=seq, kind=kind, name=name, status=result.status,
            duration_ms=result.duration_ms, external_calls=external_calls,
            error_codes=tuple(e.code for e in result.errors), evidence_count=len(result.evidence_ids),
            tokens_in=tokens_in, tokens_out=tokens_out, spend=spend, planner_turns=planner_turns,
            retries=_retries(result) if kind == 'tool' else 0, source_id=source_id)
        self.records.append(record)
        self._log.info(record.line())
        return record

    def tool(self, name: str, result: ToolResult, *, source_id: str, arguments: dict,
             external_calls: int = 0) -> TraceRecord:
        """One store reader call, named after its function, with the gate requests it used."""
        return self.record('tool', name, arguments, result, source_id=source_id,
                           external_calls=external_calls)

    def planner_turn(self, decision: PlannerDecision, *, arguments: dict, duration_ms: int,
                     spend: Decimal = _ZERO, rejected: str | None = None,
                     action: PlannerAction | None = None) -> TraceRecord:
        """One counted planner turn. `rejected` is the controller's fixed reason for refusing the
        decision; refused and invalid decisions are recorded as malformed replies."""
        info = {'decision': decision.kind, 'reason': decision.reason[:MAX_REASON]}
        if action is not None:
            info.update(action_id=action.action_id, tool=action.tool, source_id=action.source_id,
                        target=action.target)
        usage = decision.usage
        if usage is not None:
            info.update(model=usage.model[:100], input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens, cost=str(usage.cost.amount),
                        currency=usage.cost.currency)
        problem = rejected or ('the planner returned an invalid decision' if decision.kind == 'invalid'
                               else None)
        errors = ([ToolError(code='INVALID_SCHEMA', message=f'planner: {problem}', retryable=False)]
                  if problem else [])
        result = ToolResult(status='error' if errors else 'ok', data=info, errors=errors,
                            duration_ms=duration_ms)
        return self.record('planner', 'planner', arguments, result, planner_turns=1, spend=spend,
                           tokens_in=usage.input_tokens if usage else 0,
                           tokens_out=usage.output_tokens if usage else 0)

    def stage(self, name: str, *, duration_ms: int = 0, data: dict | None = None, external_calls: int = 0,
              planner_turns: int = 0, spend: Decimal = _ZERO) -> TraceRecord:
        """The end of one run stage, with its counts."""
        result = ToolResult(status='ok', data=data or {}, duration_ms=duration_ms)
        return self.record('stage', f'stage:{name}', {}, result, external_calls=external_calls,
                           planner_turns=planner_turns, spend=spend)

    def finish(self, status: str, stop_reason: str) -> None:
        self.status, self.stop_reason = status, stop_reason

    def summary(self) -> dict:
        """Run metrics: calls per source, errors per code, retries, tokens, spend and durations."""
        calls, errors, durations = {}, {}, {}
        for r in self.records:
            if r.source_id is not None and r.external_calls:
                calls[r.source_id] = calls.get(r.source_id, 0) + r.external_calls
            for code in r.error_codes:
                errors[code] = errors.get(code, 0) + 1
            durations[r.name] = durations.get(r.name, 0) + r.duration_ms
        tools = [r for r in self.records if r.kind == 'tool']
        return {
            'run_id': self.run_id, 'status': self.status, 'stop_reason': self.stop_reason,
            'records': len(self.records), 'tool_calls': len(tools),
            'tool_errors': sum(1 for r in tools if r.status != 'ok'),
            'planner_turns': sum(r.planner_turns for r in self.records),
            'external_calls': sum(r.external_calls for r in self.records),
            'retries': sum(r.retries for r in self.records),
            'calls_per_source': dict(sorted(calls.items())),
            'errors_per_code': dict(sorted(errors.items())),
            'tokens': {'input': sum(r.tokens_in for r in self.records),
                       'output': sum(r.tokens_out for r in self.records)},
            'spend': {'amount': str(sum((r.spend for r in self.records), _ZERO)), 'currency': 'USD'},
            'duration_ms': {'tools': sum(r.duration_ms for r in tools),
                            'planner': sum(r.duration_ms for r in self.records if r.kind == 'planner'),
                            'by_name': dict(sorted(durations.items()))},
        }
