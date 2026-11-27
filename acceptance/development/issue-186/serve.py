"""Local API plus the delivery quote loop for the #186 walkthrough, fully offline.

The real FastAPI app, accounts and chat API serve the built frontend from a local SQLite database.
In the same process, the real QuoteService plans and sends quotes through the real SourceGate and a
fixture ledger. The gate's network transport is a scripted Shopify Storefront for synthetic sellers
only (City-Shop, Street-A, Street-B). No retailer, model or provider key is contacted.

Each request the scripted storefront receives is logged as JSON lines with the street replaced by the
saved location's label, so the log can be published as evidence without the invented street.

    python acceptance/development/issue-186/serve.py --database-url sqlite:///walk.db --port 8186 \
        --requests requests.jsonl
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]

import httpx2  # noqa: E402
import uvicorn  # noqa: E402

from app.accounting import Ledger  # noqa: E402
from app.buyer_accounts import BuyerAccounts  # noqa: E402
from app.buyer_runs import BuyerRunService  # noqa: E402
from app.delivery_quotes import QuoteService, run_forever  # noqa: E402
from app.main import create_app  # noqa: E402
from app.sources import SourceGate, SourceLimiter  # noqa: E402
from app.storage import Storage  # noqa: E402
from test_delivery_quotes import SELLERS, registry_entry, storefront  # noqa: E402
from test_source_gate import respond  # noqa: E402

LOCATIONS = json.loads((ROOT / 'tests/fixtures/accounts.json').read_text(encoding='utf-8'))['locations']
LABELS = {location['street']: location['label'] for location in LOCATIONS}
OFFICE = LOCATIONS[1]['street']


def reply(name, variant, address):
    """The scripted seller answers. Street-B conflicts for one address and cannot deliver to the other."""
    price = SELLERS[name][0]
    if name == 'Street-A':
        return storefront(price, (('Express', '12.00'), ('Standard', '5.00')), variant=variant)
    if name == 'Street-B':
        if address.get('address1') == OFFICE:
            return storefront(price, (), variant=variant, groups=0)
        return storefront('950', variant=variant)
    return storefront(price, variant=variant)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--database-url', required=True)
    parser.add_argument('--port', type=int, default=8186)
    parser.add_argument('--requests', required=True, help='JSON lines log of what the scripted storefront received')
    args = parser.parse_args(argv)
    if not args.database_url.startswith('sqlite:///'):
        raise SystemExit('Refusing: the walkthrough runs only on a local SQLite database')
    work = Path(tempfile.mkdtemp(prefix='techscout-186-serve-'))
    registry = work / 'sources.json'
    entries = [registry_entry(name, detail) for name, (_, _, detail) in SELLERS.items() if detail]
    registry.write_text(json.dumps({'schema_version': 1, 'live_release': {
        'admitted_source_ids': [entry['id'] for entry in entries]}, 'sources': entries}), encoding='utf-8')
    log = Path(args.requests)
    lock = threading.Lock()

    def storefront_transport(request):
        data = json.loads(request.content)
        address = data['variables']['input']['buyerIdentity']['deliveryAddressPreferences'][0]['deliveryAddress']
        variant = data['variables']['input']['lines'][0]['merchandiseId'].rsplit('/', 1)[1]
        name = next(name for name, (_, sku, _) in SELLERS.items() if sku == variant)
        record = {'seller': name, 'host': request.url.host, 'method': request.method, 'path': request.url.path,
                  'variant': variant, 'quantity': data['variables']['input']['lines'][0]['quantity'],
                  'city': address['city'], 'street_sent': 'address1' in address,
                  'saved_location': LABELS.get(address.get('address1')) if 'address1' in address else None,
                  'cookie_sent': 'cookie' in request.headers}
        with lock, log.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(record) + '\n')
        # A framed, streamed reply, as the offline tests' transport gives the gate.
        return respond(200, reply(name, variant, address), 'application/json')(request)

    storage = Storage(args.database_url, work / 'evidence')
    runs = BuyerRunService(storage)
    accounts = BuyerAccounts(runs, quote_registry=registry)
    ledger = Ledger(storage, 'walkthrough-186', fixture=True)
    ledger.provision()
    gate = SourceGate._for_tests(registry, transport=httpx2.MockTransport(storefront_transport),
                                 resolver=lambda host, port: ['104.18.0.10'], limiter=SourceLimiter())
    stop = threading.Event()
    threading.Thread(target=run_forever, args=(QuoteService(accounts, gate=gate, ledger=ledger, registry=registry), stop),
                     kwargs={'idle_seconds': 0.3}, name='delivery-quotes', daemon=True).start()
    try:
        uvicorn.run(create_app(storage, buyer_service=runs, account_service=accounts, cookie_secure=False),
                    host='127.0.0.1', port=args.port, log_level='warning')
    finally:
        stop.set()


if __name__ == '__main__':
    main()
