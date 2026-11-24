"""Run a frozen or calibration case against an isolated evaluation deployment."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from urllib.parse import urlsplit
from uuid import UUID

import httpx2

from app.buyer_runs import TERMINAL
from app.contracts_v2 import ComparisonWorkspace, RunSnapshot
from eval.runners import trial_request
from eval.deployment import trace_directory


def run(client, case, side, repetition, *, clock=time.monotonic, sleep=time.sleep):
    request = trial_request(case,side,repetition)
    origin = str(client.base_url).rstrip('/')
    binding = client.get('/_evaluation/binding')
    binding.raise_for_status()
    settings = binding.json()
    if settings['side'] != side or settings['corpus'] != ('cold' if side=='full-cold' else 'warm'):
        raise ValueError('Evaluation deployment does not match the requested configuration')
    session = client.get('/api/session')
    session.raise_for_status()
    headers = {'origin':origin,'x-csrf-token':session.json()['csrf_token']}
    def post(path,data):
        reply = client.post(path,json=data,headers=headers)
        reply.raise_for_status()
        return RunSnapshot.model_validate(reply.json())
    started = clock()
    stamp = datetime.now(timezone.utc).isoformat()
    current = post('/api/runs',request.model_dump(mode='json'))
    path = '/api/runs/'+current.run.run_id
    trace, answered = [], set()
    outcome = None
    while True:
        elapsed = clock()-started
        if current.run.request_id != request.request_id or current.request.request_id != request.request_id:
            raise ValueError('Snapshot belongs to another request')
        trace.append(dict(seconds=elapsed,snapshot=current.model_dump(mode='json')))
        if elapsed>=180:
            outcome = 'timeout'
            if current.status not in TERMINAL:
                post(path+'/cancel',{'run':current.run.model_dump(mode='json')})
            break
        if current.status in TERMINAL:
            break
        if current.clarification:
            key = (current.run.run_revision,current.clarification.clarification_id)
            if key not in answered:
                answer = case.get('clarification_answer') if len(answered)<2 else None
                current = post(path+'/clarify',dict(run=current.run.model_dump(mode='json'),
                    clarification_id=key[1],answer=answer or 'No other requirements. Please continue with what I gave you.'))
                answered.add(key)
                continue
        sleep(min(1,180-elapsed))
        reply = client.get(path)
        reply.raise_for_status()
        current = ComparisonWorkspace.model_validate(reply.json()).current
    if outcome is None:
        outcome = 'recommended' if current.recommendations else (
            'abstained' if current.status=='complete' else current.status)
    return dict(case_id=case['case_id'],side=side,repetition=repetition,run_id=current.run.run_id,
                started_at=stamp,outcome=outcome,trace=trace,deployment=settings)


def attach_worker_trace(record, directory, *, wait_seconds=8):
    """The API declares its side before dispatch. Verify the worker that actually ran it."""
    run = record['trace'][-1]['snapshot']['run']
    path = directory / f"{UUID(run['run_id'])}-r{run['run_revision']}.json"
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            trace = json.loads(path.read_bytes())
            break
        except (FileNotFoundError, json.JSONDecodeError):
            if time.monotonic() >= deadline:
                record['worker_error'] = 'Missing completed worker trace'
                if record['outcome'] not in {'timeout', 'cancelled', 'error'}:
                    record['outcome'] = 'error'
                return
            time.sleep(.1)
    if trace.get('run') != run or trace.get('versions', {}).get('evaluation') != record['deployment']:
        record.update(outcome='error', worker_error='Worker trace differs from the API trial binding')
        return
    record['worker'] = trace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dataset',type=Path)
    parser.add_argument('case_id')
    parser.add_argument('side',choices=['full-warm','full-cold','single-agent-warm','fixed-heuristic-warm'])
    parser.add_argument('--repetition',type=int,choices=[1,2,3],default=1)
    parser.add_argument('--origin',required=True,help='Local buyer API with the selected controller and trial database')
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    parsed=urlsplit(args.origin)
    if parsed.scheme not in {'http','https'} or parsed.hostname not in {'localhost','127.0.0.1','::1'} or parsed.username:
        parser.error('Provide the local isolated evaluation deployment')
    if args.side=='full-cold' and args.repetition!=1:
        parser.error('The frozen cold comparison has one repetition')
    case=next(c for c in json.loads(args.dataset.read_text(encoding='utf-8'))['cases'] if c['case_id']==args.case_id)
    trial_request(case,args.side,args.repetition)
    directory = trace_directory()
    with args.output.open('x',encoding='utf-8') as out, httpx2.Client(base_url=args.origin,timeout=5) as client:
        try:
            record=run(client,case,args.side,args.repetition)
            attach_worker_trace(record, directory)
        except (httpx2.HTTPError, ValueError) as exc:
            record=dict(case_id=args.case_id,side=args.side,repetition=args.repetition,
                        outcome='error',error=type(exc).__name__)
        json.dump(record,out,ensure_ascii=False)


if __name__=='__main__':
    main()
