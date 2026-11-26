"""Serve one archived product revision with the comparison adapter on loopback."""
import argparse
from contextlib import nullcontext
from dataclasses import replace
import json
from pathlib import Path
import threading

import uvicorn

from app.buyer_flow import execute, production_dependencies
from app.buyer_runs import BuyerRunService
from app.buyer_sources import gate_fetcher
from app.domestic_buyer import Listing
from app.domestic_buyer_sources import DomesticBuyerSources
from app.domestic_discovery import DISCOVERY_ENV
from app.inference import InferenceConfig, build_inference, load_env_file
from app.main import create_app
from app.run_worker import execute_one
from app.source_registry import DEFAULT_REGISTRY
from app.storage import Storage
from bank import Bank, SharedGate, SharedScrape, SharedSearch, digest
from browser import Session


class PairedSources(DomesticBuyerSources):
    def read(self, request, url, job, run, *, earlier=None, room):
        result = super().read(request, url, job, run, earlier=earlier, room=room)
        listing = Listing(str(url), result.source_id, earlier)
        session = self.session
        session.reads[listing.url] = result
        if (not self.control or result.status not in ('ok', 'failed')
                or listing.url not in self.gate.documents or job.cancelled or room() < 3):
            return result
        if result.offers and all(o.stock.state == 'known' and o.source_variant_id
                                 and all(c.state != 'unknown' for c in o.costs) for o in result.offers):
            return result
        if not session.scoped(listing):
            return result
        if listing.url in session.states:
            session.perform(listing, 'refresh', job, room=room)
        else:
            session.perform(listing, 'open', job, room=room)
        state = session.states.get(listing.url)
        if not state:
            return result
        # Inspect one offered selection. A buyer constraint and any earlier SKU
        # constrain the choice; the frozen evaluator still decides eligibility.
        actions = session.actions(listing)
        wanted = set(c.casefold() for c in request.allowed_colors)
        for key, row in state['options'].items():
            action = digest([state['selected'], key])
            if (action not in actions or (earlier and earlier.source_variant_id != row['variant'])
                    or (wanted and wanted.isdisjoint(s.casefold() for s in row['labels']))):
                continue
            return session.perform(listing, action, job, room=room)[1]
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arm', choices=('control', 'agent'), required=True)
    parser.add_argument('--mode', choices=('live', 'replay'), required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--bank', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--destination-ref', required=True)
    parser.add_argument('--input', required=True)
    parser.add_argument('--city', required=True)
    parser.add_argument('--profile', type=Path, required=True)
    args = parser.parse_args()
    load_env_file(args.env_file, names=('OPENROUTER_API_KEY', *DISCOVERY_ENV))
    config = json.loads((Path.cwd() / 'config/evaluation.json').read_bytes())['evaluation_inference']
    deps = production_dependencies(fixed_registry_path=DEFAULT_REGISTRY,
                                   inference=build_inference(InferenceConfig.model_validate(config)))
    if deps.ledger.project != 'techscout-december-2026':
        raise ValueError('Incorrect live ledger project')
    bank = Bank(args.bank, args.mode, args.work / 'events.jsonl')
    original = deps.research.workers.domestic_tools
    profile = json.loads(args.profile.read_bytes())
    channels = profile['channels']
    if args.mode == 'live' and not set(channels).issubset(original.connectors):
        raise ValueError('Locked provider configuration unavailable')
    if args.mode == 'live':
        rates = {k: str(c.rate) for k, c in original.connectors.items() if hasattr(c, 'rate')}
        if original.fallback:
            rates['managed_scrape'] = str(original.fallback.rate)
        if rates != profile['rates'] or bool(original.fallback) != profile['managed_scrape']:
            raise ValueError('Provider rates or scrape access changed since preflight')
    search = SharedSearch(original, bank)
    search.connectors = {name: original.connectors.get(name) for name in channels}
    gate = SharedGate(original.gate, bank)
    fallback = SharedScrape(original.fallback, bank, gate) if profile['managed_scrape'] else None
    search.fallback = fallback
    deps.research.workers.domestic_tools = search
    storage = Storage()
    deps.source.fetcher = gate_fetcher(gate, DEFAULT_REGISTRY)
    sources = PairedSources(storage, gate, original.directory, fallback=fallback)
    sources.control = args.arm == 'control'
    deps.domestic_sources = sources
    deps.traces = args.work / 'traces'
    service = BuyerRunService(storage)
    # #197 only accepts <=120 in its constructor. The same lease envelope
    # is injected at the adapter boundary; the ledger still enforces 120 s.
    service.limit_ms = 300_000
    if hasattr(service, 'agent_limit_ms'):
        service.agent_limit_ms = 300_000
    submitted = False

    def controller(context):
        nonlocal submitted
        request = context.request
        intent = request.intent
        text = str(intent.url) if intent.kind == 'product_link' else getattr(intent, 'model', None)
        if (submitted or text != args.input or request.fulfillment.destination.city != args.city
                or request.requirements.category != 'phone' or request.fulfillment.mode != 'delivery'
                or (getattr(request, 'implementation', 'control') == 'single_agent') != (args.arm == 'agent')):
            return context.publish(status='error', stop_reason='Unexpected development submission')
        submitted = True
        with Session(context.request, context.claim.run,
                     sources, bank, args.destination_ref) as session:
            sources.session = session
            if not sources.control:
                deps.retailer_browser = lambda request, run: nullcontext(session)
                deps.tavily_preflight_ok = 'tavily' in channels
            execute(context, deps)

    stop = threading.Event()
    def work():
        while not stop.wait(.1):
            execute_one(service, controller)
    worker = threading.Thread(target=work)
    worker.start()
    try:
        uvicorn.run(create_app(storage, buyer_service=service, cookie_secure=False),
                    host='127.0.0.1', port=args.port, log_level='warning')
    finally:
        stop.set()
        worker.join()


if __name__ == '__main__':
    main()
