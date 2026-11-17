"""Shared WooCommerce reader (MVP 05): contract, adversarial replies, failures and configuration.

Uses the minimized captures and the synthetic variants in tests/fixtures/stores/adversarial.
"""
import dataclasses
from decimal import Decimal
import html
import json
from pathlib import Path
import unittest
from unittest import mock

from app import adapters
from app.adapters import beytech, class_cellulars, mojitech
from app.adapters.base import PolicyFact
from app.adapters.woocommerce import (
    ERROR_REASONS, FIXED_REASONS, PAYMENT_CONFLICT, Claim, PageFacts, WooCommerceAdapter, _OfferBuilder,
    form_key, merge_policy_fact, read_html, read_page, role, visible_text,
)
from app.contracts import Offer, ToolError
from app.sources import FetchOutcome

from . import (BEY_API, CLASS_API, CLASS_FAQ, CLASS_PAGE, EN_DASH, MOJI_API, MOJI_PAGE, ROUTES, FakeFetch,
               ReaderTestCase, UnroutedURL, by_id, evidence, fixture, query, reasons, routes)

ROOT = Path(__file__).resolve().parents[2]
CLASS = class_cellulars.ADAPTER
INJECTION = 'IGNORE PREVIOUS INSTRUCTIONS'
QUERIES = {'delivery': query(), 'pickup': query(mode='pickup', city='Tyre')}


def bad_markup():
    """html.parser raises AssertionError on some declarations; the readers must report it."""
    return mock.patch('app.adapters.woocommerce._PageParser.feed', side_effect=AssertionError('markup'))


def error(code, message, retryable=False):
    return ToolError(code=code, message=message, retryable=retryable)


def schema(message):
    return error('INVALID_SCHEMA', f'class_cellulars: {message}')


def form_json(page: bytes) -> str:
    """The variation form's JSON, as the reader sees it."""
    [form] = read_html(page).forms
    return form['data-product_variations']


def with_form(page: bytes, entries: list) -> bytes:
    """The page with its variation form's entries replaced, escaped as WooCommerce writes them."""
    start = page.index(b'data-product_variations="') + len(b'data-product_variations="')
    end = page.index(b'"', start)
    escaped = html.escape(json.dumps(entries, separators=(',', ':')), quote=True).encode()
    return page[:start] + escaped + page[end:]


def page_with(key, value, vid=19116):
    """The Class Cellulars page with one variation entry's value changed."""
    page = fixture('class_cellulars/page.html')
    entries = json.loads(form_json(page))
    entry = next(e for e in entries if e['variation_id'] == vid)
    if key.startswith('attribute_'):
        entry['attributes'][key] = value
    else:
        entry[key] = value
    return with_form(page, entries)


def api_with(change):
    """The Class Cellulars API reply with its parent product changed in place by change()."""
    records = json.loads(fixture('class_cellulars/api.json'))
    change(records[1])
    return json.dumps(records).encode()


def condition(variation, slug, name):
    return {'id': 0, 'name': 'Condition', 'taxonomy': 'pa_condition' if variation else None,
            'has_variations': variation, 'terms': [{'id': 0, 'name': name, 'slug': slug}]}


def condition_option(slug):
    """A Condition variation attribute, with every variation set to slug."""
    def change(parent):
        parent['attributes'].append(condition(True, slug, slug))
        for v in parent['variations']:
            v['attributes'].append({'name': 'Condition', 'value': slug})
    return change


def repeated_record(records: list, differ: bool) -> list:
    """The Class Cellulars reply with variation record 19116 returned twice."""
    again = json.loads(json.dumps(records[0]))
    if differ:
        again['prices']['price'] = '110000'
    return records + [again]


def repeated_list_entry(records: list, differ: bool) -> list:
    """The variation list naming 19116 twice."""
    entry = json.loads(json.dumps(next(v for v in records[1]['variations'] if v['id'] == 19116)))
    if differ:
        entry['attributes'][1]['value'] = 'white'
    records[1]['variations'].append(entry)
    return records


def repeated_option(records: list, differ: bool) -> list:
    """19116's list entry naming its color twice (two attributes can share a display label)."""
    entry = next(v for v in records[1]['variations'] if v['id'] == 19116)
    entry['attributes'].append({'name': 'color', 'value': 'white' if differ else 'blue'})
    return records


class ContractTests(ReaderTestCase):
    def test_every_offer_validates_binds_evidence_and_cites_returned_snapshots(self):
        for source, adapter in adapters.ADAPTERS.items():
            for name, q in QUERIES.items():
                with self.subTest(source=source, route=name):
                    result = adapter.search_candidates(FakeFetch(routes(source)), q)
                    self.assertContract(result)
                    self.assertEqual(result.tool_result.status, 'ok')
                    self.assertEqual(len(result.snapshots), 2)  # one per successful fetch
                    for offer in result.offers:
                        self.assertIsInstance(offer, Offer)
                        self.assertEqual(offer.fulfillment, q.fulfillment)
                        for e in offer.evidence:  # bound to this source, product and actual variant
                            self.assertIn(e.product_id, (None, offer.variant.product_id))
                            self.assertIn(e.variant_id, (None, offer.variant.variant_id))

    def test_a_store_without_a_branch_list_lists_no_pickup_branch(self):
        for source in ('mojitech', 'beytech'):
            for branch in (None, 'mar-elias'):  # mar-elias is a Class Cellulars branch
                with self.subTest(source=source, branch=branch):
                    q = query(mode='pickup', city='Beirut', branch_id=branch)
                    result = adapters.get_adapter(source).search_candidates(FakeFetch(routes(source)), q)
                    self.assertTrue(result.offers)
                    self.assertEqual({len(o.pickup_stock) for o in result.offers}, {0})

    def test_every_policy_topic_a_route_offers_can_change_that_field(self):
        """A run fetches a page for the paths it may settle, so each must really be settled."""
        bare = {'payment_basis': 'payment terms are not published on the product page',
                'arrival': 'delivery time is not published'}
        for source, adapter in adapters.ADAPTERS.items():
            for mode in ('delivery', 'pickup'):
                fetch = FakeFetch(routes(source))
                offer = adapter.search_candidates(fetch, query(mode=mode)).offers[0]
                for policy_id, settles in adapter.policy_topics_for(mode).items():
                    with self.subTest(source=source, mode=mode, policy=policy_id):
                        self.assertLessEqual(set(settles), set(adapter.policy_topics[policy_id]))
                        [document] = adapter.fetch_policy(fetch, policy_id).policies
                        if not settles:
                            self.assertEqual(adapter.apply_policy(offer, document), offer)
                        for path in settles:
                            self.assertIn(path, bare, 'a policy can settle only these fields')
                            data = offer.model_dump(mode='json')
                            data[path] = {'state': 'unknown', 'reason': bare[path]}
                            before = Offer.model_validate(data)
                            after = adapter.apply_policy(before, document)
                            self.assertNotEqual(getattr(after, path), getattr(before, path), path)
                        for path in set(adapter.policy_topics[policy_id]) - set(settles):
                            after = adapter.apply_policy(offer, document)
                            self.assertEqual(getattr(after, path), getattr(offer, path), path)

    def test_fixed_wording_only(self):
        self.assertFalse(FIXED_REASONS & ERROR_REASONS)
        for reason in FIXED_REASONS | ERROR_REASONS:
            self.assertRegex(reason, r'^[A-Za-z][A-Za-z ;]+$')

    def test_injected_product_name_stays_data(self):
        fetch = FakeFetch(routes('class_cellulars', {CLASS_API: 'adversarial/injected_name.api.json'}))
        result = CLASS.search_candidates(fetch, query())
        self.assertContract(result)
        self.assertEqual(fetch.urls(), [CLASS_API, CLASS_PAGE])  # the URL in the name is never fetched
        for offer in result.offers:
            self.assertEqual(offer.variant.model.value, 'iPhone 17')  # our table's name, never the text
            self.assertIn(INJECTION, offer.variant.raw_attributes['product_name'])
            [model] = evidence(offer, offer.variant.model.evidence_ids)
            self.assertEqual((model.json_pointer, model.excerpt), ('/1/name', None))
            facts = offer.model_dump_json(exclude={'variant': {'raw_attributes'}})
            self.assertNotIn(INJECTION, facts)
            self.assertNotIn('evil.example', facts)
            self.assertFalse([r for r in reasons(offer) if 'IGNORE' in r or 'evil' in r])
        self.assertNotIn('evil.example', json.dumps(result.tool_result.model_dump(mode='json')))


class AdversarialTests(ReaderTestCase):
    def search(self, overrides, source='class_cellulars', q=None):
        fetch = FakeFetch(routes(source, overrides))
        result = adapters.get_adapter(source).search_candidates(fetch, q or query())
        self.assertContract(result)
        return fetch, result

    def test_changed_api_schema_gives_invalid_schema_and_no_offers(self):
        price_fields = 'price fields are not in the expected shape'
        one_product = 'store API reply does not hold exactly one variable product'
        cases = [('adversarial/missing_prices.api.json', price_fields),
                 ('adversarial/minor_unit_string.api.json', price_fields),
                 (b'{"products": []}', 'store API reply is not a product list'),
                 (b'[]', one_product)]
        records = json.loads(fixture('class_cellulars/api.json'))
        cases.append((json.dumps(records + [records[1]]).encode(), one_product))
        changes = [((1, 'id'), 99999, 'product ID differs from the configuration'),
                   ((0, 'prices', 'price'), '1030.00', 'variation record is not in the expected shape'),
                   ((1, 'attributes', 0, 'terms'), 'none', 'product fields are not in the expected shape')]
        for path, value, message in changes:
            changed = json.loads(fixture('class_cellulars/api.json'))
            node = changed
            for key in path[:-1]:
                node = node[key]
            node[path[-1]] = value
            cases.append((json.dumps(changed).encode(), message))
        # A variation record, list entry or option name that repeats with a different value.
        product_fields = 'product fields are not in the expected shape'
        for repeat, message in ((repeated_record, 'variation record is not in the expected shape'),
                                (repeated_list_entry, product_fields), (repeated_option, product_fields)):
            cases.append((json.dumps(repeat(json.loads(fixture('class_cellulars/api.json')), True)).encode(),
                          message))
        for body, message in cases:
            with self.subTest(message=message, body=body if isinstance(body, str) else body[:40]):
                fetch, result = self.search({CLASS_API: body})
                self.assertEqual((result.tool_result.status, result.tool_result.errors, result.offers),
                                 ('error', [schema(message)], ()))
                self.assertEqual(fetch.urls(), [CLASS_API])  # no page request without a usable product
                self.assertEqual(len(result.snapshots), 1)  # the body that arrived is still kept

    def test_a_model_option_must_agree_with_the_product_name(self):
        terms = [{'id': 0, 'slug': s, 'name': n} for s, n in (('iphone-17', 'iPhone 17'),
                 ('iphone-17-pro', 'iPhone 17 Pro'), ('iphone-17-plus', 'iPhone 17 Plus'),
                 ('iphone-17-256gb', 'iPhone 17 256GB'))]
        cases = [('iphone-17', 'known', None, 'iPhone 17'),
                 ('iphone-17-pro', 'conflict', 'model option and product name disagree', None),
                 ('iphone-17-plus', 'unknown', 'model option is not a known model', None),  # a qualifier: another phone
                 ('iphone-17-256gb', 'unknown', 'model option is not a known model', None),  # exact, not a substring
                 ('', 'unknown', 'variation accepts any model option', None)]
        for slug, state, reason, value in cases:
            with self.subTest(option=slug):
                records = json.loads(fixture('class_cellulars/api.json'))
                parent = records[1]
                parent['attributes'].append({'id': 9, 'name': 'Model', 'taxonomy': 'pa_model',
                                             'has_variations': True, 'terms': terms})
                for entry in parent['variations']:
                    option = slug if entry['id'] == 19116 else 'iphone-17'
                    entry['attributes'].append({'name': 'Model', 'value': option})
                _, result = self.search({CLASS_API: json.dumps(records).encode()})
                offers = by_id(result)
                model = offers['19116'].variant.model
                self.assertEqual((model.state, model.reason, model.value), (state, reason, value))
                self.assertEqual(offers['20648'].variant.model.value, 'iPhone 17')
                if state == 'conflict':
                    self.assertEqual(model.alternatives, ['iPhone 17 Pro', 'iPhone 17'])
                if state == 'known':
                    cited = evidence(offers['19116'], model.evidence_ids)
                    self.assertEqual([(e.scope, e.json_pointer) for e in cited], [
                        ('variant', '/1/variations/1/attributes/3/value'), ('product', '/1/name')])

    def test_identical_api_repeats_are_one_statement(self):
        for repeat in (repeated_record, repeated_list_entry, repeated_option):
            with self.subTest(repeat=repeat.__name__):
                body = json.dumps(repeat(json.loads(fixture('class_cellulars/api.json')), False)).encode()
                _, result = self.search({CLASS_API: body})
                self.assertEqual((result.tool_result.status, len(result.offers)), ('ok', 4))
                offer = by_id(result)['19116']
                self.assertEqual((offer.variant.color.value, offer.price.value.amount),
                                 ('Blue', Decimal('1030.00')))

    def test_ajax_only_variations_keep_identity_and_leave_prices_unknown(self):
        _, result = self.search({CLASS_PAGE: 'adversarial/ajax_only.page.html'})
        self.assertEqual((result.tool_result.status, result.tool_result.errors),
                         ('partial', [schema('product page loads variations separately')]))
        self.assertEqual(len(result.offers), 4)
        for offer in result.offers:
            for fact in (offer.price, offer.purchasable, offer.online_stock):
                self.assertEqual((fact.state, fact.reason),
                                 ('unknown', 'product page variation data could not be read'))
            self.assertEqual(offer.variant.color.state, 'known')  # from the API's variation list
            self.assertEqual(offer.costs[0].reason, 'VAT statement is not tied to a known price')
            self.assertEqual(offer.warranty.months.value, 18)  # the page text was still read
        self.assertEqual(result.tool_result.data['variants_without_price'], 4)

    def test_page_and_api_disagreement_is_a_conflict(self):
        _, result = self.search({CLASS_PAGE: 'adversarial/price_mismatch.page.html'})
        offers = by_id(result)
        mismatch = offers['19116']
        self.assertEqual((mismatch.price.state, mismatch.price.reason),
                         ('conflict', 'store reports different prices for this variation'))
        self.assertEqual([(m.amount, m.currency) for m in mismatch.price.alternatives],
                         [(Decimal('1030.00'), 'USD'), (Decimal('1100.00'), 'USD')])  # API first, then page
        cited = evidence(mismatch, mismatch.price.evidence_ids)
        self.assertEqual([(e.json_pointer, e.excerpt) for e in cited], [
            ('/0/prices/price', None), ('/0/prices/currency_code', None),
            (None, 'variation 19116 display_price 1100'), ('/1/prices/currency_code', None)])
        self.assertEqual(mismatch.costs[0].reason, 'VAT statement is not tied to a known price')
        self.assertEqual(offers['18398'].price.value.amount, Decimal('1030.00'))
        self.assertEqual(result.tool_result.data['variants_without_price'], 1)

    def test_api_record_flags_that_disagree_with_the_page_are_conflicts(self):
        records = json.loads(fixture('class_cellulars/api.json'))
        self.assertEqual(records[0]['id'], 19116)
        records[0].update(is_purchasable=False, is_in_stock=False)
        _, result = self.search({CLASS_API: json.dumps(records).encode()})
        offer = by_id(result)['19116']
        for fact, reason, pointer, excerpt in (
                (offer.purchasable, 'store reports different purchasability for this variation',
                 '/0/is_purchasable', 'variation 19116 is_purchasable true variation_is_active true'),
                (offer.online_stock, 'store reports different stock for this variation',
                 '/0/is_in_stock', 'variation 19116 is_in_stock true')):
            self.assertEqual((fact.state, fact.reason, fact.alternatives),
                             ('conflict', reason, [False, True]))
            self.assertEqual([(e.json_pointer, e.excerpt) for e in evidence(offer, fact.evidence_ids)],
                             [(pointer, None), (None, excerpt)])
        agreeing = by_id(self.search({})[1])['19116']  # the captured record agrees and is cited too
        self.assertEqual((agreeing.online_stock.state, agreeing.online_stock.value), ('known', True))
        self.assertEqual([e.json_pointer for e in evidence(agreeing, agreeing.online_stock.evidence_ids)],
                         ['/0/is_in_stock', None])

    def test_a_backorder_is_never_in_stock(self):
        backorder = '<p class="stock available-on-backorder">Available on backorder</p>\n'
        cases = [  # (availability line, backorders allowed, API is_on_backorder) -> stock reason or True
            (backorder, None, None, 'variation is on backorder',
             'variation 19116 availability class available-on-backorder'),
            # backorders without notification show no line, but a theme may hide it: only a doubt
            ('', True, None, 'variation may be on backorder',
             'variation 19116 backorders_allowed true, no in-stock line'),
            ('<p class="stock in-stock">60 in stock</p>\n', True, None, True, None),
            (None, None, True, 'variation is on backorder', None),
        ]
        for line, allowed, api, expected, marker in cases:
            with self.subTest(line=line, allowed=allowed, api=api):
                page = fixture('class_cellulars/page.html')
                entries = json.loads(form_json(page))
                entry = next(e for e in entries if e['variation_id'] == 19116)
                self.assertIs(entry['is_in_stock'], True)  # WooCommerce writes true for a backorder
                if line is not None:
                    entry['availability_html'] = line
                if allowed is not None:
                    entry['backorders_allowed'] = allowed
                records = json.loads(fixture('class_cellulars/api.json'))
                if api is not None:
                    records[0]['is_on_backorder'] = api
                _, result = self.search({CLASS_PAGE: with_form(page, entries),
                                         CLASS_API: json.dumps(records).encode()})
                stock = by_id(result)['19116'].online_stock
                if expected is True:
                    self.assertEqual((stock.state, stock.value), ('known', True))
                else:
                    self.assertEqual((stock.state, stock.reason), ('unknown', expected))
                    excerpts = [e.excerpt for e in evidence(by_id(result)['19116'], stock.evidence_ids)]
                    self.assertIn('variation 19116 is_in_stock true', excerpts)
                    if marker is not None:  # the record names the marker actually seen
                        self.assertIn(marker, excerpts)

    def test_identity_disagreements_and_unreadable_options_stay_unresolved(self):
        def several_sizes(p):
            p['name'] = 'iPhone 17 256GB / 512GB'
            p['attributes'] = [a for a in p['attributes'] if a['name'] != 'storage-capacity']

        def used_product_new_option(p):
            condition_option('new')(p)
            p['attributes'].append(condition(False, 'used', 'Used'))

        storage, sim = 'attribute_pa_storage-capacity', 'attribute_pa_sim-type'
        maker = {'id': 1, 'name': 'Samsung', 'slug': 'samsung'}
        cases = [
            ('storage_gb', {CLASS_API: api_with(lambda p: p.update(name='iPhone 17 128GB'))},
             'conflict', 'storage option and product name disagree', [256, 128]),
            ('storage_gb', {CLASS_PAGE: page_with(storage, '')},
             'unknown', 'variation accepts any storage option', []),
            ('storage_gb', {CLASS_PAGE: page_with(storage, '256')},
             'unknown', 'storage option is not a recognized size', []),
            ('storage_gb', {CLASS_API: api_with(several_sizes)},
             'unknown', 'product name lists several storage sizes', []),
            ('sim', {CLASS_PAGE: page_with(sim, 'esim')}, 'conflict',
             'store reports different SIM types for this variation', ['esim-only', 'physical+esim']),
            ('sim', {CLASS_PAGE: page_with(sim, 'dual-sim')}, 'unknown', 'SIM option is not recognized', []),
            ('sim', {CLASS_PAGE: page_with(sim, '')}, 'unknown', 'variation accepts any SIM option', []),
            ('color', {CLASS_PAGE: page_with('attribute_pa_color', 'black')},
             'conflict', 'store reports different colors for this variation', ['Black', 'Blue']),
            ('manufacturer', {CLASS_API: api_with(lambda p: p['tags'].append(maker))},
             'conflict', 'product data names different manufacturers', ['Apple', 'Samsung']),
            ('model', {CLASS_API: api_with(lambda p: p.update(name='Mystery Phone'))},
             'unknown', 'product name does not name a known model', []),
            ('condition', {CLASS_API: api_with(used_product_new_option)},
             'conflict', 'store reports different conditions for this variation', ['new', 'used']),
            ('condition', {CLASS_API: api_with(condition_option('open-box'))},
             'unknown', 'condition option is not recognized', []),
            ('condition', {CLASS_API: api_with(condition_option(''))},
             'unknown', 'variation accepts any condition option', []),
        ]
        for name, overrides, state, reason, alternatives in cases:
            with self.subTest(fact=name, reason=reason):
                offer = by_id(self.search(overrides)[1])['19116']
                fact = offer.condition if name == 'condition' else getattr(offer.variant, name)
                self.assertEqual((fact.state, fact.reason, list(fact.alternatives)),
                                 (state, reason, alternatives))
        # A product-level statement covers every variation.
        new = api_with(lambda p: p['attributes'].append(condition(False, 'new', 'New')))
        stated = by_id(self.search({CLASS_API: new})[1])['19116']
        self.assertEqual((stated.condition.state, stated.condition.value), ('known', 'new'))

    def test_flag_disagreement_and_unreadable_prices(self):
        page = fixture('class_cellulars/page.html')
        entries = json.loads(form_json(page))
        entries.append(dict(entries[0], is_in_stock=False, is_purchasable=False))  # 20648 again
        offer = by_id(self.search({CLASS_PAGE: with_form(page, entries)})[1])['20648']
        for fact, reason in ((offer.purchasable, 'store reports different purchasability for this variation'),
                             (offer.online_stock, 'store reports different stock for this variation')):
            self.assertEqual((fact.state, fact.reason, fact.alternatives),
                             ('conflict', reason, [True, False]))
        no_currency = api_with(lambda p: p['prices'].pop('currency_code'))
        cases = [({CLASS_API: no_currency}, 'currency is not declared'),
                 ({CLASS_PAGE: page_with('display_price', '1030', vid=20648)}, 'page price is not a number'),
                 ({CLASS_PAGE: page_with('display_price', -1, vid=20648)},
                  'page price is not a valid amount')]
        for overrides, reason in cases:
            with self.subTest(reason=reason):
                offer = by_id(self.search(overrides)[1])['20648']
                self.assertEqual((offer.price.state, offer.price.reason, offer.price.value),
                                 ('unknown', reason, None))

    def test_warranty_statements_that_disagree_are_a_conflict(self):
        claim = Claim((None, 18), 'Warranty 18 months')  # a page claim against the name's 2 years
        config = dataclasses.replace(beytech.CONFIG, page_facts=lambda text: PageFacts(warranty=claim))
        result = WooCommerceAdapter(config).search_candidates(FakeFetch(routes('beytech')), query())
        self.assertContract(result)
        months = result.offers[0].warranty.months
        self.assertEqual((months.state, months.reason, months.alternatives),
                         ('conflict', 'warranty statements disagree', [18, 24]))

    def test_a_variation_that_does_not_validate_is_reported_not_returned(self):
        with mock.patch.object(_OfferBuilder, 'condition', return_value={'state': 'known'}):  # no value
            _, result = self.search({})
        self.assertEqual((result.tool_result.status, result.offers), ('error', ()))
        self.assertEqual(result.tool_result.errors, [schema('a variation could not be read')] * 4)

    def test_repeated_page_entries(self):
        _, result = self.search({CLASS_PAGE: 'adversarial/duplicate_variation.page.html'})
        offers = by_id(result)
        self.assertEqual(len(result.offers), 4)
        identical = offers['18398']  # repeated identically: one entry, one citation
        self.assertEqual(identical.price.state, 'known')
        self.assertEqual(len(identical.price.evidence_ids), 2)  # the page entry and the currency
        differing = offers['20648']
        self.assertEqual((differing.price.state, [m.amount for m in differing.price.alternatives]),
                         ('conflict', [Decimal('1030.00'), Decimal('1050.00')]))
        excerpts = [e.excerpt for e in evidence(differing, differing.price.evidence_ids) if e.excerpt]
        self.assertEqual(excerpts, ['variation 20648 display_price 1030',
                                    'variation 20648 display_price 1050'])
        self.assertEqual(differing.variant.color.value, 'Lavender')  # facts that agree stay known
        self.assertEqual(len(differing.variant.color.evidence_ids), 3)  # both entries and the API list

    def test_vat_statements_that_disagree_leave_tax_unknown(self):
        page = fixture('class_cellulars/page.html').replace(b'"valueAddedTaxIncluded":true',
                                                            b'"valueAddedTaxIncluded":false')
        _, result = self.search({CLASS_PAGE: page})
        self.assertEqual(result.offers[0].costs[0].reason, 'VAT statements on the product page disagree')

    def test_related_product_vat_does_not_apply_at_same_price(self):
        original = fixture('class_cellulars/page.html')
        start = original.index(b'<p class="price">')
        end = original.index(b'</p>', start) + len(b'</p>')
        price_block = original[start:end]
        suffix = b'<small class="woocommerce-price-suffix">(Inc. VAT)</small>'
        related = b'<aside class="related"><h2>Another product</h2>' + price_block + b'</aside>'
        target = original.replace(suffix, b'')
        for position in (b'</body>', b'<form class="variations_form'):
            with self.subTest(position=position):
                page = target.replace(position, related + position)
                _, result = self.search({CLASS_PAGE: page})
                for offer in result.offers:
                    self.assertEqual(offer.price.value.amount, Decimal('1030.00'))
                    self.assertEqual(offer.costs[0].state, 'unknown')
                    self.assertEqual(offer.costs[0].evidence_ids, [])

    def test_price_suffix_requires_summary_for_the_target_product(self):
        target = fixture('class_cellulars/page.html')
        start = target.index(b'<p class="price">')
        end = target.index(b'</p>', start) + len(b'</p>')
        price_block = target[start:end]
        other_summary = (b'<div class="summary entry-summary">' + price_block
                         + b'<form class="variations_form" data-product_id="53"></form></div>')
        page = target[:start] + target[end:]
        page = page.replace(b'</body>', other_summary + b'</body>')
        _, result = self.search({CLASS_PAGE: page})
        self.assertTrue(result.offers)
        self.assertTrue(all(offer.costs[0].state == 'unknown' for offer in result.offers))

    def test_vat_statement_must_follow_this_price(self):
        # The suffix follows a price this product does not have, so it does not cover this offer.
        page = fixture('class_cellulars/page.html').replace(b'1,030.00&nbsp;', b'1,050.00&nbsp;')
        _, result = self.search({CLASS_PAGE: page})
        self.assertEqual(result.offers[0].price.value.amount, Decimal('1030.00'))
        self.assertEqual(result.offers[0].costs[0].reason, 'VAT statement is not tied to a known price')

    def test_variation_missing_from_page_and_options_left_open(self):
        page = fixture('class_cellulars/page.html')
        entries = [e for e in json.loads(form_json(page)) if e['variation_id'] != 19117]
        entries[0]['attributes']['attribute_pa_color'] = ''  # WooCommerce: any color
        _, result = self.search({CLASS_PAGE: with_form(page, entries)})
        offers = by_id(result)
        self.assertEqual(len(result.offers), 4)  # the API still lists 19117
        missing = offers['19117']
        for fact in (missing.price, missing.purchasable, missing.online_stock):
            self.assertEqual((fact.state, fact.reason), ('unknown', 'variation is not on the product page'))
        self.assertEqual(missing.variant.color.value, 'White')
        self.assertEqual((offers['20648'].variant.color.state, offers['20648'].variant.color.reason),
                         ('unknown', 'variation accepts any color option'))

    def test_page_only_variation_is_read(self):
        page = fixture('class_cellulars/page.html')
        entries = json.loads(form_json(page))
        extra = dict(entries[0], variation_id=30000)
        extra['attributes'] = dict(extra['attributes'], attribute_pa_color='black')
        _, result = self.search({CLASS_PAGE: with_form(page, entries + [extra])})
        offer = by_id(result)['30000']
        self.assertEqual((offer.variant.color.value, offer.price.value.amount), ('Black', Decimal('1030.00')))
        self.assertEqual(result.tool_result.data['variants_seen'], 5)

    def test_unreadable_markup_and_missing_form(self):
        with bad_markup():
            _, result = self.search({})
        self.assertEqual(result.tool_result.errors, [schema('product page markup could not be read')])
        self.assertEqual({o.price.reason for o in result.offers}, {'product page could not be read'})
        page = fixture('class_cellulars/page.html')
        no_form = 'product page has no variation form for this product'
        cases = [(b'<html><body><p>No form here</p></body></html>', no_form),
                 (page.replace(b'data-product_id="18392"', b'data-product_id="1"'), no_form),
                 (page.replace(b'data-product_variations="[{', b'data-product_variations="[NaN,{'),
                  'product page variation data is not in the expected shape')]
        for body, message in cases:
            with self.subTest(message=message):
                _, result = self.search({CLASS_PAGE: body})
                self.assertEqual(result.tool_result.errors, [schema(message)])
                self.assertEqual({o.price.state for o in result.offers}, {'unknown'})


class FailureTests(ReaderTestCase):
    def test_api_failure_skips_the_page(self):
        timeout = error('TIMEOUT', 'class_cellulars: attempt passed its soft deadline', retryable=True)
        fetch = FakeFetch(routes('class_cellulars', {CLASS_API: timeout}))
        result = CLASS.search_candidates(fetch, query())
        outcome = (result.tool_result.status, result.tool_result.errors, result.offers, result.snapshots)
        self.assertEqual(outcome, ('error', [timeout], (), ()))
        self.assertEqual(fetch.urls(), [CLASS_API])
        self.assertEqual(result.tool_result.data['fetches'][0]['error_code'], 'TIMEOUT')

    def test_page_failure_keeps_identity_and_says_the_page_was_not_read(self):
        failure = error('NETWORK_ERROR', 'mojitech: DNS lookup failed', retryable=True)
        fetch = FakeFetch(routes('mojitech', {MOJI_PAGE: failure}))
        result = mojitech.ADAPTER.search_candidates(fetch, query())
        self.assertContract(result)
        self.assertEqual((result.tool_result.status, result.tool_result.errors), ('partial', [failure]))
        self.assertEqual(len(result.offers), 12)
        offer = result.offers[0]
        for fact in (offer.price, offer.purchasable, offer.arrival, offer.payment_basis):
            self.assertEqual(fact.reason, 'product page could not be read')
        self.assertEqual({c.reason for c in offer.costs}, {'product page could not be read'})
        self.assertEqual((offer.variant.model.value, offer.variant.color.value), ('iPhone 17', 'bleu'))
        self.assertEqual([s.media_type for s in result.snapshots], ['application/json'])

    def test_model_label_rules_out_products_before_fetching(self):
        from app.adapters.class_cellulars import CONFIG
        from app.adapters.woocommerce import WooCommerceAdapter
        fixed = WooCommerceAdapter(CONFIG)
        fetch = FakeFetch(routes('class_cellulars'))
        result = fixed.search_candidates(fetch, query(model='iPhone 17 Pro'))
        self.assertEqual((result.tool_result.status, result.offers, fetch.calls), ('ok', (), []))
        unknown_model = fixed.search_candidates(fetch, query(model='Galaxy S26 Ultra'))
        self.assertEqual(len(unknown_model.offers), 4)  # not ruled out; identity decides later

    def test_unknown_offer_or_policy_fetches_nothing(self):
        fetch = FakeFetch({})
        others = ('mojitech:91706:91724', 'class_cellulars:99999:1', 'class_cellulars:18392', '', None)
        for offer_id in others:
            with self.subTest(offer_id=offer_id):
                result = CLASS.fetch_offer(fetch, offer_id, query())
                self.assertEqual((result.tool_result.status, result.tool_result.errors, result.offers),
                                 ('error', [schema('unknown offer')], ()))
        for policy_id in ('refund_returns', 'FAQ', None):
            with self.subTest(policy_id=policy_id):
                result = CLASS.fetch_policy(fetch, policy_id)
                self.assertEqual((result.tool_result.errors, result.policies),
                                 ([schema('unknown policy')], ()))
        self.assertEqual(fetch.calls, [])
        with self.assertRaises(UnroutedURL):  # the fake really refuses unrouted URLs
            FakeFetch({})('class_cellulars', CLASS_FAQ, expect='html')

    def test_fetch_offer_rereads_one_variation(self):
        fetch = FakeFetch(routes('class_cellulars'))
        result = CLASS.fetch_offer(fetch, 'class_cellulars:18392:19117', query())
        self.assertContract(result)
        self.assertEqual(fetch.urls(), [CLASS_API, CLASS_PAGE])
        [offer] = result.offers
        self.assertEqual((offer.offer_id, offer.variant.color.value, offer.price.value.amount),
                         ('class_cellulars:18392:19117', 'White', Decimal('1030.00')))
        self.assertEqual({k: result.tool_result.data[k] for k in ('offer_ids', 'variants_seen')},
                         {'offer_ids': ['class_cellulars:18392:19117'], 'variants_seen': 4})
        gone = CLASS.fetch_offer(FakeFetch(routes('class_cellulars')), 'class_cellulars:18392:11111', query())
        self.assertEqual((gone.tool_result.status, gone.tool_result.errors, gone.offers),
                         ('error', [schema('offer is not listed')], ()))

    def test_fetch_offers_rereads_each_product_once(self):
        fetch = FakeFetch(routes('class_cellulars'))
        ids = ['class_cellulars:18392:19117', 'class_cellulars:18392:19116', 'class_cellulars:18392:11111',
               'mojitech:1:2', 'class_cellulars:99999:1', 'not an offer ID']
        result = CLASS.fetch_offers(fetch, ids, query())
        self.assertContract(result)
        self.assertEqual(fetch.urls(), [CLASS_API, CLASS_PAGE])  # one read covers both variations
        self.assertEqual(sorted(o.offer_id for o in result.offers),
                         ['class_cellulars:18392:19116', 'class_cellulars:18392:19117'])
        self.assertEqual(result.tool_result.status, 'partial')
        self.assertEqual(sorted(e.message for e in result.tool_result.errors),
                         sorted([schema('unknown offer').message] * 3 + [schema('offer is not listed').message]))
        # fetch_offer is the same read for one ID
        one = CLASS.fetch_offer(FakeFetch(routes('class_cellulars')), 'class_cellulars:18392:19117', query())
        many = CLASS.fetch_offers(FakeFetch(routes('class_cellulars')), ['class_cellulars:18392:19117'], query())
        self.assertEqual((one.tool_result.status, one.tool_result.data, one.offers),
                         (many.tool_result.status, many.tool_result.data, many.offers))
        for nothing in ([], 'class_cellulars:18392:19117', None):
            with self.subTest(offer_ids=nothing):
                empty = CLASS.fetch_offers(fetch, nothing, query())
                self.assertEqual((empty.tool_result.status, empty.tool_result.errors, empty.offers),
                                 ('error', [schema('unknown offer')], ()))
        self.assertEqual(fetch.urls(), [CLASS_API, CLASS_PAGE])  # unknown IDs cost no request
        self.assertEqual(adapters.fetch_offers(FakeFetch(routes('class_cellulars')), 'class_cellulars',
                                               ['class_cellulars:18392:19117'], query()).offers, many.offers)

    def test_refused_request_mid_read_is_reported_not_invented(self):
        fetch = FakeFetch(routes('class_cellulars'), cap=4)
        CLASS.search_candidates(fetch, query())
        CLASS.fetch_policy(fetch, 'faq')
        # The API request is the store's fourth this run; the page request would be the fifth.
        result = CLASS.fetch_offer(fetch, 'class_cellulars:18392:19117', query())
        self.assertContract(result)
        self.assertEqual((result.tool_result.status, [e.code for e in result.tool_result.errors]),
                         ('partial', ['BUDGET_EXHAUSTED']))
        [offer] = result.offers
        self.assertEqual((offer.price.state, offer.price.reason),
                         ('unknown', 'product page could not be read'))

    def test_failed_policy_fetch(self):
        failure = error('HTTP_ERROR', 'http:404 class_cellulars: permanent HTTP status')
        result = CLASS.fetch_policy(FakeFetch(routes('class_cellulars', {CLASS_FAQ: failure})), 'faq')
        outcome = (result.tool_result.status, result.tool_result.errors, result.policies, result.snapshots)
        self.assertEqual(outcome, ('error', [failure], (), ()))
        with bad_markup():
            unreadable = CLASS.fetch_policy(FakeFetch(routes('class_cellulars')), 'faq')
        self.assertEqual(unreadable.tool_result.errors, [schema('policy page markup could not be read')])
        self.assertEqual(len(unreadable.snapshots), 1)

    def test_policy_page_with_no_recognized_statement_is_reported(self):
        moved = b'<html><body><h1>FAQ</h1><p>Our policies have moved. Please call us.</p></body></html>'
        result = CLASS.fetch_policy(FakeFetch(routes('class_cellulars', {CLASS_FAQ: moved})), 'faq')
        tool = result.tool_result
        self.assertEqual((tool.status, tool.errors, tool.data['facts']),
                         ('partial', [schema('policy page states nothing the reader recognizes')], 0))
        self.assertEqual((len(result.policies), len(result.snapshots)), (1, 1))
        [document] = result.policies
        offer = CLASS.search_candidates(FakeFetch(routes('class_cellulars')), query()).offers[0]
        self.assertEqual(CLASS.apply_policy(offer, document), offer)


class PolicyTests(ReaderTestCase):
    def offer_and_faq(self):
        fetch = FakeFetch(routes('class_cellulars'))
        offer = CLASS.search_candidates(fetch, query()).offers[0]
        return offer, CLASS.fetch_policy(fetch, 'faq').policies[0]

    def test_policy_applies_only_to_its_own_source(self):
        offer, faq = self.offer_and_faq()
        other = mojitech.ADAPTER.fetch_policy(FakeFetch(routes('mojitech')), 'refund_returns').policies[0]
        with self.assertRaises(ValueError):
            CLASS.apply_policy(offer, other)
        with self.assertRaises(ValueError):
            mojitech.ADAPTER.apply_policy(offer, other)
        with self.assertRaises(ValueError):
            CLASS.apply_policy(offer, dataclasses.replace(faq, policy_id='shipping'))

    def test_a_card_price_and_a_cash_only_faq_conflict(self):
        offer, faq = self.offer_and_faq()
        data = offer.model_dump(mode='json')
        record = dict(next(e for e in data['evidence'] if e['field_path'] == 'arrival'),
                      field_path='payment_basis', evidence_id='synthetic-card-terms', excerpt='Card price')
        data['payment_basis'] = {'state': 'known', 'value': 'card', 'evidence_ids': ['synthetic-card-terms']}
        data['evidence'].append(record)
        after = CLASS.apply_policy(Offer.model_validate(data), faq)
        terms = after.payment_basis
        self.assertEqual((terms.state, terms.alternatives, terms.reason),
                         ('conflict', ['card', 'cash'], 'payment statements disagree'))
        self.assertEqual(terms.evidence_ids[0], 'synthetic-card-terms')

    def test_a_policy_cannot_settle_what_the_page_left_open(self):
        fetch = FakeFetch(routes('mojitech'))
        offer = mojitech.ADAPTER.search_candidates(fetch, query()).offers[0]
        page = offer.payment_basis  # the footer states cash and card, so the basis stays open
        self.assertEqual((page.state, page.reason, len(page.evidence_ids)), (
            'unknown', 'store accepts cash and card; price terms per method are not published', 1))
        policy = mojitech.ADAPTER.fetch_policy(fetch, 'refund_returns').policies[0]
        cash_only = PolicyFact('payment', {'methods': ['cash'], 'only': True}, 'We only accept cash')
        policy = dataclasses.replace(policy, facts=(cash_only,))
        after = merge_policy_fact(offer, policy, cash_only, 'payment_basis', 'cash',
                                  conflict_reason=PAYMENT_CONFLICT)
        terms = after.payment_basis
        self.assertEqual((terms.state, terms.reason), ('unknown', 'payment statements disagree'))
        self.assertEqual(terms.evidence_ids[0], page.evidence_ids[0])
        self.assertEqual([e.excerpt for e in evidence(after, terms.evidence_ids)][1], 'We only accept cash')
        again = merge_policy_fact(after, policy, cash_only, 'payment_basis', 'cash',
                                  conflict_reason=PAYMENT_CONFLICT)
        self.assertEqual(again, after)
        unchanged = merge_policy_fact(offer, policy, cash_only, 'payment_basis', 'cash',
                                      conflict_reason=PAYMENT_CONFLICT, only_if_unknown=True)
        self.assertEqual(unchanged, offer)


class ParsingTests(unittest.TestCase):
    def test_visible_text(self):
        body = ('<html><head><title>Hidden title</title></head><body><script>var x = "<p>no</p>";</script>'
                '<style>p {}</style><noscript>off</noscript><svg><text>icon</text></svg><template><p>t</p>'
                '</template><p>Within <strong>Beirut</strong>: 1&nbsp;day</p><ul><li>a</li><li>b</li></ul>'
                'x<br>y &amp; z</body></html>').encode()
        self.assertEqual(visible_text(body), 'Within Beirut: 1 day a b x y & z')
        # A head that is never closed ends where the body starts.
        self.assertEqual(visible_text(b'<head><title>t</title><body><p>shown</p>'), 'shown')

    def test_attribute_names(self):
        self.assertEqual([role(n) for n in ('pa_storage-capacity', 'M Size', 'SIM Type', 'ESIM', 'pa_color')],
                         ['storage capacity', 'm size', 'sim type', 'esim', 'color'])
        self.assertEqual([form_key('M Size', None), form_key('ESIM', None), form_key('Color', 'pa_color')],
                         ['attribute_m-size', 'attribute_esim', 'attribute_pa_color'])

    def test_repeated_attributes_keep_the_first_like_browsers(self):
        body = (b'<form class="variations_form cart" data-product_id="7" data-product_id="8" '
                b'data-product_variations="[]" data-product_variations="false"></form>')
        url = 'https://x.example/'
        outcome = FetchOutcome('x', url, (url,), 1, 1, final_url=url, body=body, response_sha256='0' * 64)
        page, problem = read_page(outcome, '7')
        self.assertEqual((page.items, problem), ({}, None))


class ConfigurationTests(unittest.TestCase):
    def test_adapters_match_the_reviewed_registry(self):
        registry = json.loads((ROOT / 'config' / 'sources.json').read_text(encoding='utf-8'))
        entries = {s['id']: s for s in registry['sources']}
        self.assertEqual(set(adapters.ADAPTERS), set(registry['live_release']['admitted_source_ids']))
        for source, adapter in adapters.ADAPTERS.items():
            with self.subTest(source=source):
                config = adapter.config
                urls = [u for p in config.products for u in (p.api_url, p.page_url)]
                urls += [p.url for p in config.policies]
                self.assertEqual(sorted(urls), sorted(entries[source]['allowed_endpoints']))
                self.assertEqual(sorted(urls), sorted(ROUTES[source]))
                self.assertEqual((adapter.source_id, adapter.adapter_version), (source, f'{source}/1'))
                self.assertIs(adapters.get_adapter(source), adapter)
        with self.assertRaises(KeyError):
            adapters.get_adapter('abed_tahan')

    def test_package_functions_delegate(self):
        fetch = FakeFetch(routes('beytech'))
        self.assertEqual(len(adapters.search_candidates(fetch, 'beytech', query()).offers), 4)
        offer = adapters.fetch_offer(fetch, 'beytech', 'beytech:139968:140008', query()).offers[0]
        self.assertEqual(offer.offer_id, 'beytech:139968:140008')
        self.assertEqual(adapters.fetch_policy(fetch, 'beytech', 'exchange').tool_result.data['facts'], 1)
        self.assertEqual(fetch.urls()[:2], [BEY_API, beytech.CONFIG.products[0].page_url])

    def test_store_tables(self):
        self.assertEqual(dict(class_cellulars.CONFIG.sim_map),
                         {'nano sim / esim': 'physical+esim', 'esim': 'esim-only'})
        self.assertEqual(dict(mojitech.CONFIG.sim_map),
                         {'esim act': 'esim-only', 'esim act tech+': 'esim-only',
                          'abm 2years warranty': None})
        self.assertEqual(dict(beytech.CONFIG.name_sim), {'1 sim': None})  # unconfirmed
        self.assertEqual(mojitech.option_warranty('ABM 2YEARS WARRANTY'), ('ABM', 24))
        self.assertIsNone(mojitech.option_warranty('ESIM ACT'))
        name = f'Apple | iPhone 17 {EN_DASH} 1 Sim {EN_DASH} 2 Years Warranty'
        self.assertEqual(beytech.name_warranty(name), (None, 24))
        self.assertEqual((MOJI_API, MOJI_PAGE), (mojitech.CONFIG.products[0].api_url,
                                                 mojitech.CONFIG.products[0].page_url))


if __name__ == '__main__':
    unittest.main()
