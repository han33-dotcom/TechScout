"""Display the frozen domestic study schedule without submitting a request."""
import json
from pathlib import Path

from eval import freeze

ROOT = Path(__file__).resolve().parent
BASELINES = json.loads((ROOT / 'baselines.json').read_bytes())
SCOPE = json.loads((ROOT / 'protocol.json').read_bytes())['evaluation_scope']


def study_plan(*, require_pre_run=False):
    record = freeze.load()
    if require_pre_run and not record['pre_run']:
        raise ValueError('Held-out cases require the recorded and verified pre-run freeze')
    if freeze.problems():
        raise ValueError('The evaluation files do not match the recorded freeze')
    repeats = {}
    if record['pre_run']:
        settings = json.loads((ROOT / 'run-freeze.json').read_bytes())
        if settings.get('protocol_version') != record['version']:
            raise ValueError('The pre-run settings belong to a different protocol version')
        repeats = settings['repetitions']
        warm_sides = ('full-warm', 'single-agent-warm', 'fixed-heuristic-warm')
        if tuple(repeats.get(side) for side in warm_sides) not in {(3, 3, 3), (3, 3, 1), (2, 2, 1)}:
            raise ValueError('The warm repetition counts differ from the protocol budget rule')
    plan = {}
    for config in BASELINES['configurations']:
        side = config['id']
        if config['kind'] == 'automated':
            cases = BASELINES['run_order']['cold' if side == 'full-cold' else 'warm_repetition_1']
            count = repeats[side] if record['pre_run'] else config['repetitions']
            if type(count) is not int or not 1 <= count <= config['repetitions']:
                raise ValueError(f'Invalid pre-run repetition count for {side}')
        else:
            cases = BASELINES['subsets'][config['cases']]
            count = config['repetitions']
        plan[side] = {(case_id, rep) for case_id in cases for rep in range(1, count + 1)}
    return plan


def check_slot(plan, case_id, side, repetition):
    if case_id in SCOPE['retired_case_ids']:
        raise ValueError(f'{case_id} is retired from the domestic evaluation')
    if (case_id, repetition) not in plan.get(side, ()):
        raise ValueError(f'{case_id}, {side}, repetition {repetition} is outside the frozen schedule')


def summary(plan):
    return dict(domestic_cases=SCOPE['active_case_count'], retired_case_ids=SCOPE['retired_case_ids'],
                planned_per_configuration={side: len(slots) for side, slots in plan.items()})


if __name__ == '__main__':
    print(json.dumps(summary(study_plan()), indent=2))
