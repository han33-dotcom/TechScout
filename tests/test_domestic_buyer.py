"""Domestic buyer runs through the owned API/worker with synthetic catalog and live responses."""
from datetime import datetime, timedelta, timezone
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
from app.run_worker import RunContext, execute_one
from app.sources import SourceGate
from adapters import CLASS_FAQ, fixture
from adapters.test_class_catalog import API, PAGE, SEARCH, table
from test_buyer_flow import FlowCase, FakeOpenRouter, LiveFetch, inference_config
from test_writer import clone, primary, snapshot


class Router(FakeOpenRouter):
    @staticmethod
    def role(instructions):
        if instructions.startswith('Plan one public search'):
            return 'search_plan'
        return FakeOpenRouter.role(instructions)

    def answer(self, role, data):
        if role == 'search_plan':
            return dict(query_index=2, channel='brave')
        if role == 'selection':
            targets = {}
            for index, claim in enumerate(data['specifications']):
                targets.setdefault(claim['target_id'], index)
            return dict(candidates=[dict(target_id=k, claim_indexes=[v]) for k, v in list(targets.items())[:3]])
        return super().answer(role, data)


def laptop(shop, *, price='920', model=None, variant=None, origin=True, seller='first_party', unknown_fee=False,
           in_stock=True, warehouse=None, city=None, service=True):
    """A synthetic laptop offer. seller: 'first_party' (a reviewed shop's own storefront), 'listing'
    (the listing names its Lebanese merchant) or None (merchant unidentified). origin adds a
    dispatch-from-Lebanon receipt and warehouse sets the route origin; the seller rule (#185)
    ignores both. service=False leaves delivery to the destination unpublished."""
    offer = clone(primary(snapshot()), shop, name=shop, price=price, product=model)['offer']
    offer['seller']['first_party'] = seller == 'first_party'
    if warehouse:
        offer['route']['origin_country'] = warehouse
    if city:
        offer['route']['destination']['city'] = city
    if not service:
        offer['route']['item_eligible'] = dict(state='unknown', reason='Delivery to this city is unpublished')
        offer['evidence'] = [e for e in offer['evidence'] if e['field_path'] != 'route.item_eligible']
    if seller == 'listing':
        price_receipt = next(e for e in offer['evidence'] if e['field_path'] == 'price')
        for path, excerpt in (('seller.name', 'Sold by ' + shop), ('seller.market', shop + ' — Tripoli, LB')):
            offer['evidence'].append(dict(price_receipt, evidence_id=f'{shop}:{path}', field_path=path,
                                          excerpt=excerpt))
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

    def lookup(self, request, *, limit, query=None):
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

    def test_empty_catalog_exact_phone_shows_current_variants_with_unresolved_delivery(self):
        seller = self.directory.get('class_cellulars')
        self.directory.upsert(seller.model_copy(update={'role': 'domestic',
                                                        'updated_at': seller.updated_at + timedelta(microseconds=1)}))
        gate = SourceGate()

        class ScopedFetch(LiveFetch):
            def __call__(self, source_id, url, *, expect, capability=None):
                gate.check_scope(source_id, url, expect=expect, capability=capability)
                return super().__call__(source_id, url, expect=expect, capability=capability)

        self.fetch = ScopedFetch({**table(), CLASS_FAQ: fixture('class_cellulars/faq.html')})
        deps = self.deps()
        deps.inference = None
        deps.domestic_sources = Sources([], [])
        deps.research.workers.domestic_tools = self.search
        _, run = self.browse(deps, intent=dict(kind='exact_model', model='Apple iPhone 16'),
                             fulfillment=dict(mode='delivery', destination=dict(city='Beirut')))

        self.assertEqual(self.fetch.calls, [SEARCH, API, PAGE, CLASS_FAQ])
        self.assertEqual((run.coverage.retrieved, run.coverage.qualifying), (1, 0))
        self.assertFalse(run.recommendations)
        self.assertEqual(len(run.uncertain), 4)
        self.assertEqual(len({u.offer.listing_url for u in run.uncertain}), 4)
        for item in run.uncertain:
            offer = item.offer
            self.assertEqual(offer.variant.product.model, 'iPhone 16')
            self.assertEqual(offer.variant.specs.storage_gb.value, 256)
            self.assertEqual(offer.price.value.amount, Decimal('1030.00'))
            self.assertTrue(offer.stock.value)
            self.assertTrue(str(offer.listing_url).startswith(PAGE + '?attribute_pa_'))
            self.assertTrue(all(e.run == run.run for e in offer.evidence if e.field_path in ('price', 'stock')))
            # The reviewed retailer is the merchant (#185); service and the fee remain the gaps.
            self.assertEqual(offer.domestic_seller(), 'first_party')
            self.assertIn('costs.order.shipping', item.reasons)
            self.assertNotIn(domestic_buyer.SELLER_GAP, item.reasons)
            self.assertEqual(item.cost.status, 'incomplete')

    def test_sufficient_catalog_avoids_web_and_rechecks_above_old_maximum(self):
        left, right = laptop('shop-a', price='1200'), laptop('shop-b', price='922')
        run = self.run_buyer([left, right], [laptop('shop-a', price='899'), right])
        self.assertEqual(run.status, 'complete', run.stop_reason)
        self.assertEqual(self.search.calls, [])
        self.assertNotIn('search_plan', self.openrouter.roles())
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
        self.assertEqual((self.search.calls[0].channel, self.search.calls[0].language), ('brave', 'ar'))
        self.assertEqual(self.trace(run)['search_plan']['method'], 'model')
        self.assertEqual(self.onboarding.enqueue_buyer.call_args.args[0], [unknown])
        self.assertEqual([url for url, _, _ in self.sources.calls].count(str(right.listing_url)), 2)
        self.assertEqual(self.trace(run)['discovery_channels'][0]['coverage']['tavily']['new_same_variant_comparisons'], 1)

    def test_needs_plans_offer_product_terms_without_sending_addresses_or_buyer_notes(self):
        from app.domestic_search_plan import search_options
        request = v2.ShoppingRequest(request_id='dev-needs', intent=dict(kind='needs', description='private buyer notes'),
            requirements=dict(category='laptop', min_ram_gb=16),
            fulfillment=dict(mode='delivery', destination=dict(city='Beirut, private address')))
        queries = [o.query for o in search_options(request, [laptop('shop-a')])]
        self.assertTrue(all('private' not in q and 'Beirut' not in q and '16' in q for q in queries))
        self.assertFalse(any('workstation' in q for q in queries))
        self.assertTrue(any('Scout Book' in q for q in queries))

    def test_invalid_plans_fall_back_to_an_available_channel(self):
        offer = laptop('shop-a')
        self.search.connectors = {'serpapi': object()}
        answer = self.openrouter.answer
        for index, choice in enumerate((dict(query_index=99, channel='serpapi'), dict(query_index=0, channel='brave'))):
            with self.subTest(choice=choice), patch.object(self.openrouter, 'answer', side_effect=lambda role, data:
                    choice if role == 'search_plan' else answer(role, data)):
                run = self.run_buyer([offer], [offer], request_id=f'fallback-{index}')
                self.assertEqual(self.trace(run)['search_plan']['method'], 'fallback')
                self.assertEqual((self.search.calls[-1].channel, self.search.calls[-1].language), ('serpapi', 'en'))
                self.assertEqual(run.coverage.qualifying, 1)

    def test_controlled_workers_identify_their_side_and_share_final_verification(self):
        from app.accounting import Ledger
        from app.storage import Storage
        from eval import deployment, run_trial
        offer = laptop('shop-a')
        case = dict(case_id='D-planning', request=dict(schema_version=2, request_id='dev-plan',
                    intent=dict(kind='exact_model', model='Scout Book'), requirements=dict(category='laptop')))
        for side in ('full-warm', 'full-cold', 'single-agent-warm', 'fixed-heuristic-warm'):
            with self.subTest(side=side):
                deps = self.deps()
                deps.domestic_sources = Sources([offer], [offer])
                worker_storage = Storage(self.url, self.storage.snapshots.root)
                deps.domestic_sources.storage = worker_storage
                deps.ledger = Ledger(worker_storage, 'flow-fixture', fixture=True)
                deps.research.workers.domestic_tools = self.search
                db = 'techscout_eval_' + 'a' * 32
                env = dict(TECHSCOUT_EVAL_SIDE=side, DATABASE_URL='postgresql://localhost/' + db,
                           EVIDENCE_DIR=str(self.traces.parent / 'evidence'))
                def compose(*, inference, load_worker_inference):
                    self.assertFalse(load_worker_inference)
                    deps.inference = inference
                    return deps
                with (patch.dict(os.environ, env), patch.object(deployment, 'load_env_file'),
                      patch.object(deployment, 'production_dependencies', side_effect=compose),
                      patch.object(deployment, 'pinned_inference', return_value=deps.inference) as pinned):
                    deployment.expose_binding(self.app, deployment.binding())
                    record = run_trial.run(self.client, case, side, 1,
                        sleep=lambda _: self.assertTrue(execute_one(self.service, deployment.run)))
                    run_trial.attach_worker_trace(record, deployment.trace_directory())
                    self.assertEqual(record['outcome'], 'recommended', record.get('worker_error'))
                    trace = record['worker']
                    self.assertEqual(trace['verify']['verified'], [offer.offer_id])
                    self.assertEqual(trace['versions']['evaluation'], dict(side=side, database=db,
                                     corpus='cold' if side == 'full-cold' else 'warm'))
                    roles = {call['role'] for call in trace['model_calls']}
                    if side == 'fixed-heuristic-warm':
                        pinned.assert_not_called()
                        self.assertEqual(roles, set())
                        self.assertEqual(trace['search_plan']['method'], 'rule-planner')
                    elif side == 'single-agent-warm':
                        self.assertEqual(roles, {'single_agent'})
                        self.assertEqual(trace['limits']['concurrent_workers'], 1)
                    else:
                        self.assertEqual(roles, {'orchestrator', 'writer'})
                        self.assertEqual(trace['limits']['concurrent_workers'], 3)
                    record['deployment'] = dict(record['deployment'], database='different')
                    record.pop('worker')
                    run_trial.attach_worker_trace(record, deployment.trace_directory())
                    self.assertEqual(record['outcome'], 'error')

    def test_discovery_outage_keeps_direct_rechecks_and_single_seller_coverage(self):
        offer = laptop('shop-a')
        self.search = Search(outage=True)
        run = self.run_buyer([offer], [offer])
        self.assertEqual(run.coverage.qualifying, 1)
        self.assertIn('1 independent', run.stop_reason)
        self.assertIn('two-seller', run.stop_reason)
        self.assertEqual(len(self.sources.calls), 1)

    def test_seller_service_and_fee_gaps_stay_separate_without_a_warehouse(self):
        """Scenario 10 (#185): with the warehouse requirement removed, an unidentified merchant,
        unconfirmed service to Tripoli, an unpublished fee or a conflicting fee each stays its own
        gap. A foreign merchant is never a domestic candidate."""
        tripoli = dict(origin=False, warehouse='ZZ', city='Tripoli')
        unnamed = laptop('market-a', seller=None, **tripoli)  # marketplace listing, merchant unnamed
        unserved = laptop('shop-b', service=False, **tripoli)
        unpriced = laptop('shop-c', unknown_fee=True, **tripoli)
        conflicting = laptop('shop-d', **tripoli).model_dump(mode='json')
        fee = next(e for e in conflicting['evidence'] if e['field_path'] == 'costs.order.shipping')
        fee['conflicts'] = ['Shipping: USD 15 on the delivery page']
        conflicting = v2.Offer.model_validate(conflicting)
        foreign = laptop('abroad-e', seller='listing', **tripoli).model_dump(mode='json')
        foreign['seller']['market'] = 'AE'
        foreign = v2.Offer.model_validate(foreign)
        offers = [unnamed, unserved, unpriced, conflicting, foreign]
        run = self.run_buyer(offers, offers, fulfillment=dict(mode='delivery',
                                                             destination=dict(country='LB', city='Tripoli')))
        self.assertEqual(run.status, 'abstained', run.stop_reason)
        self.assertFalse(run.recommendations)
        shown = {item.offer.seller.seller_id: item for item in run.uncertain}
        self.assertNotIn('abroad-e', shown)
        self.assertNotIn(str(foreign.listing_url), [url for url, _, _ in self.sources.calls])
        reasons = {seller: ' '.join(item.reasons) for seller, item in shown.items()}
        self.assertIn(domestic_buyer.SELLER_GAP, reasons['market-a'])
        self.assertIn('Delivery of this item to the requested destination is unverified', reasons['shop-b'])
        self.assertIn('costs.order.shipping', reasons['shop-c'])
        self.assertIn('mandatory charge lacks current evidence', reasons['shop-d'])
        for seller in ('shop-b', 'shop-c', 'shop-d'):
            self.assertNotIn('seller', reasons[seller])
        for text in reasons.values():
            self.assertNotIn('Dispatch', text)
            self.assertNotIn('duty', text)
        # Useful partials: the listed price plus a published fee, never past an unknown or conflicting fee.
        self.assertEqual({seller: item.cost.status for seller, item in shown.items()},
                         dict.fromkeys(('market-a', 'shop-b', 'shop-c', 'shop-d'), 'incomplete'))
        subtotal = {seller: item.cost.known_subtotal for seller, item in shown.items()}
        self.assertEqual(subtotal['market-a'].amount, Decimal('930'))
        self.assertEqual(subtotal['shop-b'].amount, Decimal('930'))
        self.assertEqual(subtotal['shop-c'].amount, Decimal('920'))
        self.assertIsNone(subtotal['shop-d'])

    def test_link_reads_only_the_linked_seller_and_fails_closed_without_gate_response(self):
        left, right = laptop('shop-a'), laptop('shop-b')
        sources = Sources([left, right], [left, right])
        sources.stale.add(str(left.listing_url))
        run = self.run_buyer([], [], intent=dict(kind='product_link', url=str(left.listing_url)), sources=sources)
        self.assertFalse(self.search.calls)
        self.assertNotIn('search_plan', self.openrouter.roles())
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

    def test_known_lebanese_seller_qualifies_in_tripoli_with_an_unknown_warehouse(self):
        """Scenario 10 (#185). The earlier dispatch rule kept this unknown-origin offer uncertain."""
        tripoli = dict(origin=False, warehouse='ZZ', city='Tripoli')
        own_store, named = laptop('shop-a', **tripoli), laptop('shop-b', seller='listing', **tripoli)
        run = self.run_buyer([own_store, named], [own_store, named],
                             fulfillment=dict(mode='delivery', destination=dict(country='LB', city='Tripoli')))
        self.assertEqual(run.status, 'complete', run.stop_reason)
        group, = run.recommendations
        offers = {item.offer.seller.seller_id: item for item in group.offers()}
        self.assertEqual(set(offers), {'shop-a', 'shop-b'})
        self.assertEqual({item.offer.domestic_seller() for item in offers.values()}, {'first_party', 'listing'})
        for item in offers.values():
            offer = item.offer
            self.assertEqual((offer.route.origin_country, offer.route.destination.city), ('ZZ', 'Tripoli'))
            shipping, = [c for c in offer.costs if c.kind == 'shipping']
            self.assertEqual((offer.price.value.amount, shipping.amount.amount), (Decimal('920'), Decimal('10')))
            self.assertEqual(item.cost.total.amount, Decimal('930'))  # the fee is counted once
        self.assertEqual(run.coverage.qualifying, 2)
        self.assertIn('delivery by the Lebanese seller to Tripoli', run.explanation.text)
        self.assertNotIn('dispatch origin', run.explanation.text)
        self.assertNotIn('duty', run.explanation.text)

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
