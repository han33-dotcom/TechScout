"""Compare identical captured observations across both pinned imports without a model or network."""
import argparse
from contextlib import ExitStack
from dataclasses import asdict, replace
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

def probe(work, arm, path):
    sys.path[:0] = [str(work / arm), str(work / 'adapter')]
    from app.accounting import Ledger
    from app.contracts_v2 import ShoppingRequest, RunReference
    from app.directory import Directory
    from app.domestic_buyer import Listing
    from app.domestic_buyer_sources import DomesticBuyerSources
    from app.domestic_discovery import DomesticDiscovery
    from app.domestic_search_plan import search_options
    from app.source_registry import DEFAULT_REGISTRY
    from app.sources import SourceGate
    from app.storage import Storage
    from bank import Bank, SharedGate, SharedSearch, digest
    from browser import Session
    from pair import INPUTS, bank_hash

    with tempfile.TemporaryDirectory() as temp, ExitStack() as cleanup:
        directory = Path(temp)
        db = directory / 'fixture.sqlite'
        shutil.copyfile(work / 'seed.sqlite', db)
        store = Storage('sqlite:///' + str(db), directory / 'evidence')
        cleanup.callback(store.engine.dispose)
        ledger = Ledger(store, 'offline-parity', fixture=True)
        ledger.provision()
        job = ledger.open_job('offline-parity')
        gate = SourceGate(DEFAULT_REGISTRY)
        def forbidden(*args, **kwargs):
            raise AssertionError('Replay attempted live retrieval')
        gate.fetch = forbidden
        registry = Directory(store, registry_path=DEFAULT_REGISTRY)
        pages = json.loads((work / arm / 'config/maintenance.json').read_bytes())['manufacturer_pages']
        original = DomesticDiscovery.from_env(gate, registry, environ={}, directory_defaults=pages)
        bank = Bank(path, 'replay', directory / 'events.jsonl')
        search = SharedSearch(original, bank)
        model, city = INPUTS['search']
        common = dict(request_id='development', requirements=dict(category='phone'),
                      fulfillment=dict(mode='delivery', destination=dict(country='LB', city=city)))
        request = ShoppingRequest(**common, intent=dict(kind='exact_model', model=model))
        query = search_options(request, ())[0]
        report = search.search(replace(query, channel='manufacturer'), job)
        if not report.leads or report.attempts[0].status != 'ok':
            raise ValueError('Expected an independently captured directory response')
        url = INPUTS['selection'][0]
        request = ShoppingRequest(**common, intent=dict(kind='product_link', url=url))
        ref = RunReference(run_id=job.run_id, run_revision=1, request_id=request.request_id, request_revision=1)
        sources = DomesticBuyerSources(store, SharedGate(gate, bank), registry)
        listing = Listing(url, 'class_cellulars')
        with Session(request, ref, sources, bank, 'selection-city') as session:
            session.perform(listing, 'open', job, room=lambda: 60)
            state = session.states[url]
            choose = next(iter(session.actions(listing)))
            session.perform(listing, choose, job, room=lambda: 60)
            after = session.states[url]
            selected, = [v for v in after['options'].values() if v['attributes'] == after['selected']]
            if session.driver is not None or state['selected'] == after['selected']:
                raise ValueError('Expected a captured state change without a live browser')
        return dict(bank=bank_hash(path), query=query.query, channel='manufacturer',
                    search_response=digest(dict(attempts=[asdict(a) for a in report.attempts],
                                                leads=[asdict(l) for l in report.leads])), leads=len(report.leads),
                    source=listing.source_id, url=url, product=state['product'], before=state['selected'],
                    after=after['selected'], selected=selected, document=state['document'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=lambda p: Path(p).resolve(), required=True)
    parser.add_argument('--bank', type=lambda p: Path(p).resolve(), required=True)
    parser.add_argument('--arm', choices=('control', 'agent'))
    args = parser.parse_args()
    if args.arm:
        print(json.dumps(probe(args.work, args.arm, args.bank)))
        return
    rows = {arm: json.loads(subprocess.check_output([sys.executable, __file__, '--work', str(args.work),
            '--bank', str(args.bank), '--arm', arm], text=True)) for arm in ('control', 'agent')}
    if rows['control'] != rows['agent']:
        raise ValueError('The pinned imports returned different observations')
    print(json.dumps(rows, indent=2))


if __name__ == '__main__':
    main()
