"""Measure domestic development requests in isolated SQLite corpus copies using the operational ledger."""
import argparse
from contextlib import closing
from decimal import Decimal
import json
import os
from pathlib import Path
import shutil
import sqlite3
from time import monotonic

from fastapi.testclient import TestClient

from app.accounting import Ledger
from app.buyer_flow import execute, production_dependencies
from app.buyer_runs import BuyerRunService
from app.domestic_discovery import DISCOVERY_ENV, DomesticDiscovery
from app.embeddings import RATE_VARIABLE
from app.inference import load_env_file
from app.main import create_app
from app.onboarding import ActivationRegistry
from app.run_worker import execute_one
from app.storage import Storage
from eval import freeze
from eval.deployment import pinned_inference

ROOT = Path(__file__).resolve().parents[1]


def cases():
    data = json.loads((ROOT/'eval/calibration.json').read_bytes())
    domestic = {row['case_id']: row['request'] for row in data['cases'] if row['market'] == 'domestic'}
    domestic['D-H30i'] = dict(schema_version=2, request_id='dev-h30i', intent=dict(kind='exact_model',model='Soundcore H30i'),
                            requirements=dict(category='audio'),fulfillment=dict(mode='delivery',destination=dict(city='Beirut')))
    return domestic


def priority_registry(path, storage):
    audit = json.loads((ROOT/'docs/retailer-audit-2026-11-16.json').read_bytes())
    priority = {s['retailer_id'] for s in audit['retailers'] if s['priority_audit']}
    registry = ActivationRegistry(storage).read_registry()
    for source in registry['sources']:
        if source['id'] not in priority and source['status'] == 'active':
            source['status'] = 'paused'
    registry['live_release']['admitted_source_ids'] = [s for s in registry['live_release']['admitted_source_ids'] if s in priority]
    path.write_text(json.dumps(registry),encoding='utf-8')


def trial(directory, request, arm, parallelism):
    registry = None
    if arm == 'priority':
        registry = directory/'priority-sources.json'
        storage = Storage()
        try:
            priority_registry(registry, storage)
        finally:
            storage.engine.dispose()
    deps = production_dependencies(fixed_registry_path=registry,inference=pinned_inference())
    deps.research_parallelism = parallelism
    deps.versions['inference_config_sha256'] = freeze.digest(ROOT/'config/evaluation.json')
    deps.traces = directory/'traces'
    if arm == 'priority':
        deps.research.workers.domestic_tools = None
    if arm == 'outage':
        # Keep the direct source gate live. Empty search transports simulate provider loss.
        sources = deps.domestic_sources
        deps.research.workers.domestic_tools = DomesticDiscovery(sources.gate, sources.directory, {})
    storage = Storage()
    service = BuyerRunService(storage)
    try:
        with TestClient(create_app(storage,buyer_service=service,cookie_secure=True),base_url='https://testserver') as client:
            session = client.get('/api/session').json()
            headers = {'Origin':'https://testserver','X-CSRF-Token':session['csrf_token']}
            start = monotonic()
            response = client.post('/api/runs',json=request,headers=headers)
            response.raise_for_status()
            run_id = response.json()['run']['run_id']
            if not execute_one(service,lambda context: execute(context,deps)):
                raise RuntimeError('The isolated worker did not claim the development request')
            result = client.get('/api/runs/'+run_id).json()['current']
            elapsed = round((monotonic()-start)*1000)
        trace = json.loads((deps.traces/(run_id+'-r1.json')).read_bytes())
        groups = result['recommendations']
        offers = [v['offer'] for group in groups for v in (group['primary'],*group.get('additional_sellers',()))]
        return dict(run_id=run_id,started_at=trace['started_at'],status=result['status'],elapsed_ms=elapsed,
                    active_ms=trace['latency_ms'],coverage=result['coverage'],uncertain=len(result['uncertain']),
                    verified_sellers=sorted({o['seller']['seller_id'] for o in offers}),
                    verified_models=sorted({o['variant']['product']['product_id'] for o in offers}),
                    verified_totals=[g['primary']['cost']['total'] for g in groups],reason=result['stop_reason'],
                    source_outcomes=result['source_outcomes'],discovery=trace.get('discovery_channels',[]),
                    semantic_hits=trace.get('catalog_semantic_hits'),spend=trace['spend'],models=trace.get('model_calls',[]))
    finally:
        storage.engine.dispose()
        deps.domestic_sources.storage.engine.dispose()
        deps.ledger.storage.engine.dispose()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--cases',nargs='+',choices=cases(),required=True)
    parser.add_argument('--arms',nargs='+',choices=('priority','broader','outage'),default=['priority','broader'])
    parser.add_argument('--parallelism',type=int,choices=(1,2,3),default=3)
    args=parser.parse_args()
    baseline, evidence, output = args.database.resolve(strict=True), args.evidence.resolve(strict=True), args.output.resolve()
    if output.is_relative_to(evidence) or freeze.problems(ROOT):
        raise ValueError('Use a separate output directory and a verified protocol freeze')
    output.mkdir(parents=True, exist_ok=True)
    load_env_file(names=('OPENROUTER_API_KEY',RATE_VARIABLE,*DISCOVERY_ENV))
    for i,case_id in enumerate(args.cases):
        arms = args.arms if i%2 == 0 else list(reversed(args.arms))
        for arm in arms:
            folder = output/(case_id+'-'+arm+'-'+str(args.parallelism))
            if (folder/'receipt.json').is_file():
                continue
            folder.mkdir()  # Unconfirmed partial trial requires inspection before any new dispatch.
            ledger = Ledger.configured()
            try:
                remaining = Decimal('500')-Decimal(ledger.snapshot()['scopes'].get('project',[0,0,0,'0',0])[3])
                if remaining < Decimal('301'):
                    raise ValueError('Calibration would consume the held-out 300-run allowance')
            finally:
                ledger.storage.engine.dispose()
            db = folder/'app.db'
            with closing(sqlite3.connect(baseline.as_uri()+'?mode=ro',uri=True)) as src, closing(sqlite3.connect(db)) as dst:
                src.backup(dst)
            shutil.copytree(evidence,folder/'evidence')
            os.environ.update(DATABASE_URL='sqlite:///'+db.as_posix(),EVIDENCE_DIR=str(folder/'evidence'))
            result = trial(folder,cases()[case_id],arm,args.parallelism)
            result.update(case_id=case_id,arm=arm,parallelism=args.parallelism,protocol=freeze.load()['combined_sha256'])
            (folder/'receipt.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
            print(case_id,arm,result['status'],result['active_ms'],result['spend']['known_usd'],flush=True)


if __name__=='__main__':
    main()
