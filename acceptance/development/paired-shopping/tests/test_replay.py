import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))
from bank import Bank, SharedGate, SharedScrape, SharedSearch, digest
from browser import Session
from app.domestic_buyer import Listing
from app.domestic_discovery import SearchObjective
from app.source_capabilities import CapabilityRef
from app.sources import FetchOutcome


class ReplayTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        base = Path(directory.name)
        self.live = Bank(base / 'responses', 'live', base / 'capture.jsonl')
        self.replay = Bank(base / 'responses', 'replay', base / 'replay.jsonl')

    def test_provider_and_locale_scoped_misses(self):
        tool = SimpleNamespace(gate=None, directory=None, connectors={'brave': None}, fallback=None)
        search = SharedSearch(tool, self.replay)
        query = SearchObjective('Development tablet', 'Find a listing', channel='brave')
        key = dict(provider='brave', query=query.query, country='LB', language='en', limit=10, cursor=0)
        hit = dict(url='https://shop.example/dev', title='Development tablet')
        self.live.exchange('search', key, lambda: dict(attempt=dict(channel='brave', status='ok',
            reserved_usd='0.005', actual_usd='0.005'), hits=[hit]))
        job = Mock()
        attempt, hits = search._attempt(query, job, 0)
        self.assertEqual((attempt.status, hits[0].url), ('ok', hit['url']))
        job.reserve.assert_called_once()
        from dataclasses import replace
        for other in (replace(query, channel='serpapi'), replace(query, language='ar'),
                      replace(query, query='Uncaptured tablet')):
            self.assertEqual(search._attempt(other, job, 0)[0].status, 'unavailable')
        self.assertEqual(job.reserve.call_count, 1)

    def test_variant_transition_is_bound_to_prior_state_and_case(self):
        listing = Listing('https://shop.example/dev', 'shop')
        request = SimpleNamespace(fulfillment=SimpleNamespace(branch_id=None))
        run = SimpleNamespace(run_id='run')
        job = Mock(run_id='run', cancelled=False)
        ref = CapabilityRef(capability_id='dev-product', version='1')
        args = {'attribute_color': 'blue'}
        row = dict(attributes=args, labels=['Blue'], variant='42', price='99', stock=True)
        state = dict(product='10', title='Development', selected={'attribute_color': ''},
                     options={digest(args): row}, document='a' * 64, observed_at='2026-11-20T00:00:00+00:00')
        key = dict(source='shop', url=listing.url, prior=None, action='open', arguments={},
                   quantity=1, destination='development-city', branch=None)
        self.live.exchange('browser', key, lambda: dict(status='ok', state=state))
        transition = dict(key, action='select_variant', arguments=args, prior=digest(state))
        self.live.exchange('browser', transition, lambda: dict(status='ok', state=dict(state, selected=args)))
        sources = Mock()
        sources.gate.documents = {listing.url: SimpleNamespace(response_sha256=state['document'])}
        for _ in range(2):
            with Session(request, run, sources, self.replay, 'development-city') as session:
                session.scoped = lambda listing: ('shop', ref)
                session.perform(listing, 'open', job, room=lambda: 30)
                action, = session.actions(listing)
                session.perform(listing, action, job, room=lambda: 30)
                self.assertEqual(session.states[listing.url]['selected'], args)
                self.assertEqual(session.perform(listing, action, job, room=lambda: 30)[1].status, 'refused')
                self.assertIsNone(session.driver)
        with Session(request, run, sources, self.replay, 'another-city') as session:
            session.scoped = lambda listing: ('shop', ref)
            self.assertEqual(session.perform(listing, 'open', job, room=lambda: 30)[1].status, 'failed')
            self.assertFalse(session.states)
        events = [json.loads(line) for line in self.replay.events.read_text().splitlines()]
        self.assertEqual(sum(e['kind'] == 'replay_miss' for e in events), 1)

    def test_managed_document_available_to_browser_and_replay(self):
        url = 'https://shop.example/dev'
        original = Mock()
        reply = FetchOutcome('shop', url, (url,), 1, 10, final_url=url,
                             body=b'<html>development</html>', status_code=200)
        fallback = Mock()
        fallback.scrape.return_value.outcome = reply
        for mode in (self.live, self.replay):
            gate = SharedGate(original, mode)
            scrape = SharedScrape(fallback, mode, gate)
            result = scrape.scrape(Mock(), 'shop', url)
            self.assertEqual(result.outcome.body, reply.body)
            self.assertIs(gate.documents[url], result.outcome)
        self.assertEqual(original.check_scope.call_count, 2)
        fallback.scrape.assert_called_once()


if __name__ == '__main__':
    unittest.main()
