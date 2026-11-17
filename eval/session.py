"""Prepare isolated evaluation databases and reproducible manual capture records."""
import argparse
from datetime import datetime
import json
from pathlib import Path
from shutil import copytree
from uuid import uuid4

import psycopg
from psycopg import sql

from eval.runners import trial_request

ROOT = Path(__file__).resolve().parent
BASELINES = json.loads((ROOT/'baselines.json').read_text(encoding='utf-8'))


def clone_database(admin_url, template, evidence, output):
    """Clone an operator-prepared frozen warm or empty cold database for one trial."""
    target = 'techscout_eval_'+uuid4().hex
    evidence_copy = output/target/'evidence'
    evidence_copy.parent.mkdir(parents=True,exist_ok=False)
    copytree(evidence,evidence_copy)
    with psycopg.connect(admin_url,autocommit=True) as db:
        db.execute(sql.SQL('CREATE DATABASE {} TEMPLATE {}').format(sql.Identifier(target),sql.Identifier(template)))
    return target,evidence_copy


def manual_capture(case, side):
    definitions = {row['id']:row for row in BASELINES['configurations']}
    conf = definitions[side]
    if conf['kind']=='automated':
        raise ValueError('Manual capture requires an external or manual configuration')
    trial_request(case,side,1)
    if conf['cases']=='local_external' and case['market']!='domestic':
        raise ValueError('Local comparison sessions require domestic cases')
    if case['case_id'].startswith('H') and case['case_id'] not in BASELINES['subsets'][conf['cases']]:
        raise ValueError('Case is outside the frozen subset')
    procedure = {'external_service':'external_service','external_assistant':'assistant',
                 'manual':'manual_shopping'}[conf['kind']]
    record = {k:None for k in BASELINES['capture_record']['fields']}
    for k in ('queries','filters_used','unsupported_requirements','results_viewed','selected_offers',
              'verification_steps','screenshots'):
        record[k] = []
    record.update(session_id=uuid4().hex,case_id=case['case_id'],side=side)
    location = case['request'].get('fulfillment',{}).get('destination') or {}
    inputs = BASELINES['subset_inputs'].get(case['case_id'],dict(
        buyer_text=case['buyer_text'],fixed_requirements=', '.join(case['hard_constraints']),
        destination=', '.join(str(location[k]) for k in ('city','country') if location.get(k)) or 'the requested location'))
    return dict(record=record,inputs=inputs,
        timing_minutes=dict(service=20 if side=='manual-shopping' else 8,
                            extra_verification=0 if side=='manual-shopping' else 10),
        procedure=BASELINES['procedures'][procedure],fields=BASELINES['capture_record']['fields'],
        assistant_prompt=BASELINES['assistant_prompt_template'].format(**inputs) if procedure=='assistant' else None,
        follow_up=BASELINES['assistant_follow_up_template'].format(**inputs) if procedure=='assistant' else None)


def external_summary(record):
    """Record elapsed buyer effort and unsupported or inaccessible sessions explicitly."""
    if set(BASELINES['capture_record']['fields']) - record.keys():
        raise ValueError('Incomplete manual capture')
    if record['outcome'] not in {'answered','no_results','unsupported_feature','access_failure','timeout'}:
        raise ValueError('Missing external session outcome')
    def stamp(name):
        value = datetime.fromisoformat(record[name])
        if value.utcoffset() is None:
            raise ValueError('Session times require an offset')
        return value
    start, end = stamp('started_at'), stamp('service_phase_ended_at')
    if end < start:
        raise ValueError('Service phase ended before it started')
    verified = stamp('verified_result_at') if record['verified_result_at'] else None
    first = stamp('first_useful_display_at') if record['first_useful_display_at'] else None
    if (first and not start<=first<=end) or (verified and verified<start):
        raise ValueError('Result timestamp outside the session')
    steps = [datetime.fromisoformat(s['at']) for s in record['verification_steps']]
    last = max([end, *(steps or []), *([verified] if verified else [])])
    return dict(side=record['side'],case_id=record['case_id'],outcome=record['outcome'],
                service_seconds=(end-start).total_seconds(),extra_buyer_seconds=(last-end).total_seconds(),
                first_useful_seconds=(first-start).total_seconds() if first else None,
                verified_seconds=(verified-start).total_seconds() if verified else None)


def main():
    import os
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command',required=True)
    capture = commands.add_parser('capture')
    capture.add_argument('dataset',type=Path)
    capture.add_argument('case_id')
    capture.add_argument('side')
    summary = commands.add_parser('summarize')
    summary.add_argument('record',type=Path)
    clone = commands.add_parser('clone')
    clone.add_argument('template')
    clone.add_argument('evidence',type=Path)
    clone.add_argument('output',type=Path)
    args = parser.parse_args()
    if args.command=='clone':
        name,path = clone_database(os.environ['TECHSCOUT_EVAL_ADMIN_DSN'],args.template,args.evidence,args.output)
        print(json.dumps({'database':name,'evidence_dir':str(path.resolve())}))
    elif args.command=='summarize':
        print(json.dumps(external_summary(json.loads(args.record.read_text(encoding='utf-8')))))
    else:
        cases=json.loads(args.dataset.read_text(encoding='utf-8'))['cases']
        case=next((c for c in cases if c['case_id']==args.case_id),None)
        if case is None:
            parser.error('Case is absent from dataset')
        print(json.dumps(manual_capture(case,args.side),indent=2,ensure_ascii=False))


if __name__=='__main__':
    main()
