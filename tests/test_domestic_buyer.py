"""Domestic buyer runs through the owned API/worker with synthetic catalog and live responses."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import Event
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import contracts_v2 as v2
from app import buyer_flow, domestic_buyer
from app.accounting import BudgetDenied, Charge
from app.domestic_discovery import Attribution, ChannelAttempt, SearchLead, SearchReport
from app.run_worker import RunContext
from test_buyer_flow import FlowCase, FakeOpenRouter, inference_config
from test_writer import clone, primary, snapshot


class Router(FakeOpenRouter):
    def answer(self, role, data):
        if role == 'selection':
            targets = {}
            for index, claim in enumerate(data['specifications']):
                targets.setdefault(claim['target_id'], index)
            return dict(candidates=[dict(target_id=k, claim_indexes=[v]) for k, v in list(targets.items())[:3]])
        return super().answer(role, data)


def laptop(shop, *, price='920', model=None, variant=None, origin=True, unknown_fee=False, in_stock=True):
    offer = clone(primary(snapshot()), shop, name=shop, price=price, product=model)['offer']
    if model:
        offer['variant']['product']['model'] = model
    if variant:
        offer['variant']['variant_id'] = variant
    if origin:
        receipt = next(e for e in offer['evidence'] if e['field_path'] == 'route.item_eligible').copy()
        receipt.update(evidence_id=shop + ':origin', field_path='route.origin_country', excerpt='Ships from Lebanon')
        offer['evidence'].append(receipt)
    if unknown_fee:
        offer['costs'][-1] = dict(kind='shipping', state='unknown', reason='Shipping unpublished')
    offer['stock']['value'] = in_stock
    return v2.Offer.model_validate(offer)


class Sources:
    def __init__(self, earlier, current):
        self.earlier = earlier
        self.current = {str(item.listing_url): item for item in current}
        self.calls = []
        self.stale = set()

    def lookup(self, request, *, limit):
        return self.earlier[:limit]

    def read(self, request, url, job, run, *, earlier, room):
        self.calls.append((url, job.run_id, room()))
        if url not in self.current:
            return SimpleNamespace(source_id=None, status='unconfigured', offers=(), replies=set())
        offer = self.current[url]
        now = datetime.now(timezone.utc)
        records = tuple(e.model_copy(update=dict(observed_at=now, run=run, origin='runtime')) for e in offer.evidence)
        fresh = offer.model_copy(update=dict(evidence=records))
        replies = {(e.response_sha256, now) for e in records} if url not in self.stale else set()
        return SimpleNamespace(source_id=fresh.source_id, status='ok', offers=(fresh,), replies=replies)


class Search:
    def __init__(self, leads=(), outage=False):
        self.leads, self.calls, self.outage = leads, [], outage

    def search(self, objective, job, *, alternatives):
        self.calls.append(objective)
        if self.outage:
            raise TimeoutError
        return SearchReport(job.run_id, (ChannelAttempt('tavily', 'ok'),), tuple(self.leads))


def lead(url, source=None):
    return SearchLead(url, 'seller:' + (source.seller.seller_id if source else 'unknown'),
                      source.source_id if source else None, (Attribution('tavily', None, '', '', None, None),))


class DomesticBuyerTests(FlowCase):
    def setUp(self):
        super().setUp()
        self.openrouter = Router()
        self.search = Search()
        self.onboarding = Mock()
        self.onboarding.enqueue_buyer.return_value = (SimpleNamespace(status='queued'),)

    def run_buyer(self, earlier, current, *, intent=None, sources=None, **changes):
        self.sources = sources or Sources(earlier, current)
        deps = self.deps()
        deps.domestic_sources = self.sources
        deps.onboarding = self.onboarding
        deps.research.workers.domestic_tools = self.search
        req = dict(intent=intent or dict(kind='exact_model', model='Scout Book'),
                   requirements=dict(category='laptop', min_ram_gb=16),
                   budget=dict(target=dict(amount='850', currency='USD'), maximum=dict(amount='950', currency='USD')))
        _, run = self.browse(deps, **dict(req, **changes))
        self.assertNotEqual(run.status, 'error', self.trace(run))
        return run

    def test_production_entrypoint_without_chat_route_can_verify_or_report_missing_fit(self):
        offer = laptop('shop-a')
        sources = Sources([offer], [offer])
        fixture_root = Path(self.storage.snapshots.root).parent
        route = fixture_root / 'missing-inference.json'
        environment = dict(TECHSCOUT_INFERENCE_CONFIG=str(route), TECHSCOUT_CODE_VERSION='test')
        with (patch.dict(os.environ, environment, clear=True),
              patch.object(buyer_flow, 'load_env_file', return_value=()),
              patch.object(buyer_flow, '_PRODUCTION', None), patch.object(buyer_flow, 'ROOT', fixture_root),
              patch('app.accounting.Ledger.configured', return_value=self.ledger),
              patch('app.storage.Storage', return_value=self.storage),
              patch('app.domestic_buyer_sources.DomesticBuyerSources', return_value=sources),
              patch('app.openrouter.OpenRouterProvider', side_effect=AssertionError('unconfigured external call'))):
            for kind in ('product_link', 'needs', 'checkpoint'):
                if kind == 'needs':
                    # A valid reviewed configuration with a missing API key degrades in the same way.
                    route.write_text(json.dumps(dict(inference=inference_config().model_dump(mode='json'))))
                intent = (dict(kind='needs', description='Engineering course laptop') if kind == 'needs'
                          else dict(kind='product_link', url=str(offer.listing_url)))
                submitted = self.start(request_id=kind, intent=intent, requirements=dict(category='laptop'))
                context = RunContext(self.service, self.service.claim(), Event())
                if kind == 'checkpoint':
                    with self.assertLogs('techscout.buyer_flow', 'WARNING'):
                        buyer_flow.run_checkpoint(context)
                else:
                    buyer_flow.run(context)
                _, run = self.read(submitted)
                if kind == 'checkpoint':
                    self.assertEqual((run.status, run.stop_reason), ('error', buyer_flow.NOT_CONFIGURED))
                    continue
                self.assertEqual(run.explanation.source, 'fallback')
                trace = json.loads((fixture_root / 'var' / 'traces' / f'{run.run.run_id}-r1.json').read_text())
                self.assertEqual(trace['versions']['inference']['status'], 'unavailable')
                self.assertEqual(trace['writer']['status'], 'not_called')
                self.assertFalse(trace['model_calls'])
                if kind == 'product_link':
                    self.assertEqual((run.status, run.coverage.qualifying), ('complete', 1))
                else:
                    self.assertEqual(run.status, 'abstained')
                    self.assertIn('suitability', run.uncertain[0].reasons[0])

    def test_sufficient_catalog_avoids_web_and_rechecks_above_old_maximum(self):
        left, right = laptop('shop-a', price='1200'), laptop('shop-b', price='922')
        run = self.run_buyer([left, right], [laptop('shop-a', price='899'), right])
        self.assertEqual(run.status, 'complete', run.stop_reason)
        self.assertEqual(self.search.calls, [])
        self.assertEqual([v.offer.seller.seller_id for v in run.recommendations[0].offers()], ['shop-a', 'shop-b'])
        self.assertEqual(run.recommendations[0].primary.cost.total.amount, 909)
        self.assertEqual(run.coverage.qualifying, 2)
        self.assertIn('2 independent', run.stop_reason)
        provisional = next(s.provisional for s in self.published if s.provisional)
        self.assertEqual(provisional[0].observed_at, left.evidence[0].observed_at)

    def test_target_only_in_another_currency_does_not_exclude_a_verified_offer(self):
        offer = laptop('shop-a')
        run = self.run_buyer([offer], [offer], budget=dict(target=dict(amount='5000000', currency='LBP')))
        self.assertEqual(run.coverage.qualifying, 1)

    def test_stopped_worker_cannot_dispatch_a_held_lease_or_cancel_the_parent(self):
        job = self.ledger.open_job('phase-job')
        phase = domestic_buyer.PhaseJob(job, lambda: 10)
        held = phase.reserve('model', Charge(Decimal('.01')), 5)
        phase.cancel()
        with self.assertRaises(BudgetDenied):
            held.dispatch()
        held.release()
        self.assertFalse(job.cancelled)

    def test_targeted_second_seller_read_and_new_shop_handoff(self):
        left, right = laptop('shop-a'), laptop('shop-b', price='899')
        unknown = 'https://new-shop.example/product'
        self.search = Search([lead(unknown), lead(str(right.listing_url), right)])
        run = self.run_buyer([left], [left, right])
        self.assertEqual((len(self.search.calls), run.coverage.qualifying), (1, 2), run.stop_reason)
        self.assertIn('Scout Book', self.search.calls[0].query)
        self.assertNotIn('private address', self.search.calls[0].query)
        self.assertEqual(self.onboarding.enqueue_buyer.call_args.args[0], [unknown])
        self.assertEqual([url for url, _, _ in self.sources.calls].count(str(right.listing_url)), 2)
        self.assertEqual(self.trace(run)['discovery_channels'][0]['coverage']['tavily']['new_same_variant_comparisons'], 1)

    def test_discovery_outage_keeps_direct_rechecks_and_single_seller_coverage(self):
        offer = laptop('shop-a')
        self.search = Search(outage=True)
        run = self.run_buyer([offer], [offer])
        self.assertEqual(run.coverage.qualifying, 1)
        self.assertIn('1 independent', run.stop_reason)
        self.assertIn('two-seller', run.stop_reason)
        self.assertEqual(len(self.sources.calls), 1)

    def test_unknown_dispatch_and_unknown_shipping_cannot_qualify(self):
        first, second = laptop('shop-a', origin=False), laptop('shop-b', unknown_fee=True)
        run = self.run_buyer([first, second], [first, second])
        self.assertEqual(run.status, 'abstained')
        reasons = [reason for item in run.uncertain for reason in item.reasons]
        self.assertTrue(any('Dispatch from Lebanon' in reason for reason in reasons), reasons)
        self.assertTrue(any('charge' in reason or 'shipping' in reason for reason in reasons), reasons)
        self.assertFalse(run.recommendations)

    def test_link_reads_only_the_linked_seller_and_fails_closed_without_gate_response(self):
        left, right = laptop('shop-a'), laptop('shop-b')
        sources = Sources([left, right], [left, right])
        sources.stale.add(str(left.listing_url))
        run = self.run_buyer([], [], intent=dict(kind='product_link', url=str(left.listing_url)), sources=sources)
        self.assertFalse(self.search.calls)
        self.assertEqual([u for u, _, _ in self.sources.calls], [str(left.listing_url)])
        self.assertEqual((run.status, run.coverage.retrieved, len(run.uncertain)), ('abstained', 0, 1))
        self.assertEqual(run.uncertain[0].cost.status, 'incomplete')

    def test_unknown_link_is_queued_without_substituting_a_seller(self):
        url = 'https://unregistered.example/laptop'
        run = self.run_buyer([laptop('different')], [], intent=dict(kind='product_link', url=url))
        self.assertFalse(self.search.calls)
        self.assertEqual(run.status, 'abstained')
        self.assertEqual(self.onboarding.enqueue_buyer.call_args.args[0], ['https://unregistered.example/'])
        self.assertFalse(run.recommendations)

    def test_needs_models_use_current_specifications_and_group_alternative_sellers(self):
        offers = [laptop('shop-a'), laptop('shop-b', price='890'), laptop('shop-c', model='other-1'),
                  laptop('shop-d', model='other-2')]
        run = self.run_buyer(offers, offers, intent=dict(kind='needs', description='Laptop for a large spreadsheet'))
        self.assertEqual((len(run.recommendations), run.coverage.qualifying), (3, 4), run.stop_reason)
        self.assertEqual(len(run.recommendations[0].additional_sellers), 1)
        self.assertEqual(self.search.calls, [])
        self.assertIn('selection', self.openrouter.roles())

    def test_evaluator_rechecks_exactly_one_gap_and_can_recover(self):
        left, right = laptop('shop-a'), laptop('shop-b')
        port = Sources([left, right], [laptop('shop-a', unknown_fee=True), laptop('shop-b', unknown_fee=True)])
        answer = self.openrouter.answer
        def choose(role, data):
            if role == 'critique':
                chosen = data['issues'][0]['source']
                fresh = next(o for o in (left, right) if o.source_id == chosen)
                port.current[str(fresh.listing_url)] = fresh
                return dict(issue_index=0)
            return answer(role, data)
        self.openrouter.answer = choose
        run = self.run_buyer([], [], sources=port)
        self.assertEqual(run.coverage.qualifying, 1)
        self.assertEqual(len(port.calls), 3)
        self.assertEqual(self.openrouter.roles().count('critique'), 1)
        self.assertIn('evaluator_follow_up', self.trace(run))

    def test_unknown_dispatch_uses_actual_projection_sentinel(self):
        offer = laptop('shop-a', origin=False)
        offer = offer.model_copy(update={'route': offer.route.model_copy(update={'origin_country': 'ZZ'})})
        run = self.run_buyer([offer], [offer])
        self.assertFalse(run.recommendations)
        self.assertIn('Dispatch from Lebanon is unverified', ' '.join(run.uncertain[0].reasons))
        self.assertEqual(run.uncertain[0].cost.status, 'incomplete')
        self.assertFalse(any('duty' in r or 'customs' in r for r in run.uncertain[0].reasons))

    def test_same_identifier_with_conflicting_ram_does_not_make_a_seller_comparison(self):
        left, right = laptop('shop-a'), laptop('shop-b')
        value = right.model_dump()
        value['variant']['specs']['ram_gb']['value'] = 32
        right = v2.Offer.model_validate(value)
        run = self.run_buyer([left, right], [left, right])
        self.assertEqual(len(self.search.calls), 1)
        self.assertEqual(run.coverage.retrieved, 2)
        self.assertEqual(len(run.recommendations[0].offers()), 1)
        self.assertIn('two-seller comparison', run.stop_reason)

    def test_distinct_source_ids_do_not_turn_the_same_model_into_three_needs_results(self):
        offers = []
        for name in ('shop-a', 'shop-b', 'shop-c'):
            data = laptop(name, variant=name + ':sku').model_dump()
            data['variant']['product']['product_id'] = name + ':model'
            offers.append(v2.Offer.model_validate(data))
        run = self.run_buyer(offers, offers, intent=dict(kind='needs', description='Office spreadsheets'))
        self.assertEqual(len(self.search.calls), 1)
        self.assertEqual(len(run.recommendations), 1)
        self.assertFalse(run.recommendations[0].additional_sellers)

    def test_source_sku_can_acquire_a_strong_id_on_final_check(self):
        held = laptop('shop-a', variant='local:sku')
        fresh = laptop('shop-a', variant='laptop:mpn:example:mpn123')
        run = self.run_buyer([held], [fresh])
        self.assertEqual(run.recommendations[0].primary.offer.variant.variant_id, fresh.variant.variant_id)

    def test_final_read_still_fits_when_needs_research_uses_its_full_window(self):
        offer = laptop('shop-a')
        original = domestic_buyer.DomesticBuyerFlow.elapsed
        def elapsed(flow):
            return max(original(flow), flow.nonfinal_by)
        with patch.object(domestic_buyer.DomesticBuyerFlow, 'elapsed', elapsed):
            run = self.run_buyer([offer], [offer], intent=dict(kind='needs', description='Office spreadsheets'))
        self.assertEqual(run.coverage.qualifying, 1)
        self.assertGreaterEqual(self.sources.calls[0][2], domestic_buyer.FETCH_ROOM_S)

    def test_price_or_availability_drift_can_recover_with_another_seller(self):
        offers = [laptop(s) for s in ('a', 'b', 'c', 'd')]
        current = [laptop('a', price='949'), laptop('b', in_stock=False), *offers[2:]]
        run = self.run_buyer(offers, current)
        sellers = [v.offer.seller.seller_id for v in run.recommendations[0].offers()]
        self.assertEqual(sellers, ['c', 'd'])
        self.assertEqual(run.coverage.retrieved, 4)

    def test_reserve_refuses_research_and_preserves_verified_partial_results(self):
        offer = laptop('a')
        original = domestic_buyer.DomesticBuyerFlow.left
        def room(flow, deadline):
            if deadline == flow.nonfinal_by:
                return 0
            return original(flow, deadline)
        with patch.object(domestic_buyer.DomesticBuyerFlow, 'left', room):
            run = self.run_buyer([offer], [offer])
        self.assertFalse(self.search.calls)
        self.assertEqual(run.coverage.qualifying, 1)
        trace = self.trace(run)
        self.assertGreaterEqual(trace['limits']['final_by_s'] - trace['limits']['nonfinal_by_s'], 30)

    def test_timeout_during_verification_never_ranks_old_evidence(self):
        offer, other = laptop('a'), laptop('b')
        with patch.object(domestic_buyer, 'FETCH_ROOM_S', 500):
            run = self.run_buyer([offer, other], [offer, other])
        self.assertEqual(run.status, 'partial')
        self.assertFalse(run.recommendations)
        self.assertEqual(len(run.uncertain), 2)
        self.assertIn('deadline', run.stop_reason)
