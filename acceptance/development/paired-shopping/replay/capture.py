"""Independently capture a bounded development bank without either buyer's decisions or output."""
import argparse
from pathlib import Path
import json
import os
import shutil
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from pair import INPUTS, bank_hash, hash_file, save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=lambda p: Path(p).resolve(), required=True)
    parser.add_argument('--output', type=lambda p: Path(p).resolve(), required=True)
    parser.add_argument('--env-file', type=lambda p: Path(p).resolve(), default=HERE.parents[2] / '.env')
    args = parser.parse_args()
    root = args.work / 'control'
    lock = json.loads((args.work / 'lock.json').read_bytes())
    if (hash_file(args.work / 'seed.sqlite') != lock['seed'] or hash_file(args.work / 'profile.json') != lock['profile']
            or any(hash_file(root / p) != h for p, h in lock['files']['control'].items())):
        raise ValueError('Prepared control or seed differs from its lock')
    args.output.mkdir(parents=True)
    sys.path.insert(0, str(root))
    os.chdir(root)
    database = args.output / 'catalog.sqlite'
    shutil.copyfile(args.work / 'seed.sqlite', database)
    os.environ.update(DATABASE_URL='sqlite:///' + str(database), EVIDENCE_DIR=str(args.output / 'evidence'))
    from app.buyer_flow import _Flow, production_dependencies
    from app.contracts_v2 import RunReference, ShoppingRequest
    from app.domestic_buyer import Listing, PhaseJob
    from app.domestic_discovery import DISCOVERY_ENV
    from app.domestic_search_plan import search_options
    from app.inference import load_env_file
    from app.source_registry import DEFAULT_REGISTRY
    from bank import Bank, SharedGate, SharedSearch
    from browser import Session

    load_env_file(args.env_file, names=DISCOVERY_ENV)
    deps = production_dependencies(fixed_registry_path=DEFAULT_REGISTRY, load_worker_inference=False)
    if deps.ledger.project != 'techscout-december-2026':
        raise ValueError('Expected the frozen campaign')
    profile = json.loads((args.work / 'profile.json').read_bytes())
    bank = Bank(args.output / 'bank', 'live', args.output / 'events.jsonl')
    tools = deps.research.workers.domestic_tools
    search = SharedSearch(tools, bank)
    tools = deps.domestic_sources
    tools.gate = SharedGate(tools.gate, bank)
    spend = {}
    for case in ('search', 'selection'):
        value, city = INPUTS[case]
        intent = dict(kind='product_link', url=value) if case == 'selection' else dict(kind='exact_model', model=value)
        req = ShoppingRequest(request_id='dev201-' + str(uuid4()), intent=intent, requirements=dict(category='phone'),
                              fulfillment=dict(mode='delivery', destination=dict(country='LB', city=city)))
        ref = RunReference(run_id='dev201-capture-' + str(uuid4()), run_revision=1,
                           request_id=req.request_id, request_revision=1)
        job = deps.ledger.open_job(ref.run_id)
        start = time.monotonic()
        def room():
            return max(0, 85 - (time.monotonic() - start))
        phase = PhaseJob(job, room)
        if case == 'search':
            for query in search_options(req, ()):
                search.survey(query, phase, profile['channels'])
        else:
            listing = Listing(value, 'class_cellulars')
            with Session(req, ref, tools, bank, case + '-city') as session:
                session.perform(listing, 'open', phase, room=room)
                # Select the first two distinct retailer-published options from
                # empty controls. Capture one inter-variant move and reset too.
                actions = list(session.actions(listing))
                if len(actions) < 2:
                    raise ValueError('Retailer has fewer than two supported options')
                session.perform(listing, actions[0], phase, room=room)
                following = list(session.actions(listing))
                if following:
                    session.perform(listing, following[0], phase, room=room)
                session.perform(listing, 'refresh', phase, room=room)
                session.perform(listing, actions[1], phase, room=room)
        spend[case] = _Flow._spend(SimpleNamespace(deps=deps, job=job, run_ref=ref))
    save(args.output / 'receipt.json', dict(bank=bank_hash(bank.path), seed=lock['seed'], spend=spend))


if __name__ == '__main__':
    main()
