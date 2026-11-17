"""Amhaz (#56): the candidate stays blocked until the intended business is identified.

The retailer audit (#50) left Amhaz's identity unresolved, so the profile must send no request
and yield no candidate, even when the candidate site is reachable and lists priced items with a
stated condition (the dated capture in tests/fixtures/stores/amhaz/). Tripwires fail the suite
if the audit, seed or source registry starts to resolve or admit Amhaz without this profile
being replaced by a reviewed reader.
"""
from decimal import Decimal
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from app import adapters
from app.adapters import amhaz
from app.adapters.woo_catalog import CatalogResult
from app.contracts import ToolResult
from app.directory import Directory, DirectoryQuery, import_audit
from app.source_registry import get_active_source
from app.storage import Storage
from adapters import TRIPWIRES, RealNetwork, fixture
from test_storage import migrate

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = 'amhaz/candidate-2026-11-17.json'
BLOCKED_MESSAGE = 'amhaz_official_candidate: business identity is unresolved; no retrieval is configured'
REPLACE = 'Amhaz is no longer an unresolved candidate: replace the blocked profile with a reviewed reader (#56)'


def capture():
    return json.loads(fixture(CAPTURE), parse_float=Decimal)


def audit_records():
    """Amhaz records in every dated retailer audit, so a newer audit cannot resolve it unnoticed."""
    paths = sorted(ROOT.glob('docs/retailer-audit-*.json'))
    records = []
    for path in paths:
        audit = json.loads(path.read_text(encoding='utf-8'))
        records += [(path.name, r) for r in audit['retailers']
                    if 'amhaz' in ' '.join((r['retailer_id'], str(r.get('canonical_seller')))).casefold()]
    return paths, records


def seed_records():
    seeds = json.loads((ROOT / 'config/retailer-directory-seed.json').read_text(encoding='utf-8'))
    return [s for s in seeds if 'amhaz' in ' '.join((s['retailer_id'], s['name'], *s.get('aliases', ()))).casefold()]


class ServingFetch:
    """Records every call before refusing it, so a swallowed refusal still shows as a call."""

    def __init__(self):
        self.calls = []

    def __call__(self, source_id, url, *, expect, capability=None):
        self.calls.append((source_id, url, expect, capability))
        raise AssertionError('a blocked profile must not fetch')


class NoNetworkTestCase(unittest.TestCase):
    def setUp(self):
        for target in TRIPWIRES:
            patcher = mock.patch(target, side_effect=RealNetwork(target))
            patcher.start()
            self.addCleanup(patcher.stop)


class BlockedProfileTests(NoNetworkTestCase):
    def assertBlocked(self, result, fetch):
        self.assertIsInstance(result, CatalogResult)
        self.assertEqual((result.candidates, result.snapshots, fetch.calls), ((), (), []))
        tool = result.tool_result
        self.assertEqual(ToolResult.model_validate(tool.model_dump(mode='json')), tool)
        self.assertEqual((tool.status, tool.evidence_ids, tool.duration_ms), ('error', [], 0))
        self.assertEqual([(e.code, e.retryable, e.message) for e in tool.errors],
                         [('SOURCE_DISABLED', False, BLOCKED_MESSAGE)])
        self.assertEqual(tool.data, {
            'fetches': [], 'candidates_seen': 0, 'priced_candidates': 0, 'coverage_complete': False,
            'requires_current_run_verification': True, 'blocked': 'identity_unresolved',
            'identity_status': 'unresolved_user_intent'})

    def test_every_category_fails_closed_without_a_request(self):
        for category in sorted(amhaz.CATEGORIES):
            for only in (None, {('1018', '1018')}):
                with self.subTest(category=category, only=only):
                    fetch = ServingFetch()
                    self.assertBlocked(amhaz.PROFILE.search(fetch, category=category, search='iPhone 17',
                                                            only=only, run=None), fetch)

    def test_no_capability_supported_category_or_canonical_origin_is_declared(self):
        profile = amhaz.PROFILE
        self.assertEqual((profile.capabilities, profile.supported_categories, profile.canonical_origin),
                         ((), (), None))
        self.assertEqual((profile.retailer_id, profile.candidate_origin, profile.identity_status, profile.blocker),
                         ('amhaz_official_candidate', 'https://amhazofficial.com/', 'unresolved_user_intent',
                          'identity_unresolved'))
        self.assertLessEqual(set(profile.category_leads), amhaz.CATEGORIES)
        self.assertEqual(profile.adapter_version, 'amhaz-profile/1')

    def test_unsupported_input_is_refused_before_any_request(self):
        fetch = ServingFetch()
        for category, search in (('tv', 'iPhone 17'), ('Phone', 'iPhone 17'), ('phone', ''), ('phone', '   '),
                                 ('phone', 'x' * 65), ('phone', None), (None, 'iPhone 17'),
                                 (['phone'], 'iPhone 17')):
            with self.subTest(category=category, search=search), self.assertRaises(ValueError):
                amhaz.PROFILE.search(fetch, category=category, search=search)
        self.assertEqual(fetch.calls, [])


class CaptureTests(unittest.TestCase):
    """The dated capture backs each claim docs/source-coverage.md makes about the live check."""

    def test_capture_records_three_bounded_successful_reads(self):
        data = capture()
        self.assertEqual((data['retailer_id'], data['identity_status']), (amhaz.RETAILER_ID, amhaz.IDENTITY_STATUS))
        self.assertEqual([o['url'] for o in data['observations']],
                         [amhaz.CANDIDATE_ORIGIN, amhaz.CANDIDATE_ORIGIN + 'contact',
                          amhaz.CANDIDATE_ORIGIN + 'product/1018'])
        for o in data['observations']:
            with self.subTest(url=o['url']):
                self.assertEqual((o['status'], o['final_url']), (200, o['url']))
                self.assertRegex(o['sha256'], r'^[0-9a-f]{64}$')
                self.assertTrue(o['checked_at'].startswith('2026-11-17T') and o['checked_at'].endswith('+00:00'))
                self.assertEqual({k: v for k, v in o['term_counts'].items() if v}, {'shipping': 2})
        self.assertEqual((data['method']['retries'], data['method']['min_gap_s']), (0, 12))

    def test_capture_shows_identity_and_no_policy_link(self):
        data = capture()
        self.assertEqual((data['identity']['brand_name'], data['identity']['address']),
                         ('Amhaz Official', 'Saint Therese, Hadath , Lebanon'))
        self.assertIn('Amhaz Mobile Store front', data['identity']['gallery_captions'])
        self.assertEqual(data['footer']['linked_items'], {'Contact Us': '/contact'})
        self.assertTrue({'FAQs', 'Shipping', 'About Us'} <= set(data['footer']['unlinked_items']))
        text = json.dumps(data).casefold()
        for private in ('70 70 72 71', '76 70 72 71', 'whatsapp', 'email', '@'):
            self.assertNotIn(private, text)

    def test_capture_shows_the_sample_states_no_condition_and_is_out_of_stock(self):
        product = capture()['product']
        record = product['record']
        self.assertEqual((record['item_code'], record['item_name'], record['price'], record['currency'],
                          record['qty_available'], record['warehouses'], record['description']),
                         ('1018', 'IPHONE 17', Decimal(2865), 'USD', 0, [], ''))
        self.assertEqual(record['attributes']['specs'], {'Size': '256GB'})
        self.assertNotIn('Condition', record['attributes']['selectable'])
        for line in ('$2,865.00', 'Out of stock', 'Check for availability'):
            self.assertIn(line, product['visible_lines'])

    def test_capture_shows_explicit_conditions_tokens_and_ask_for_price(self):
        data = capture()
        records = {r['item_code']: r for r in data['related_records']}
        self.assertEqual({code: r['attributes']['specs'].get('Condition') for code, r in records.items()},
                         {'241': 'New', '590': 'Used', '909': 'Used', '254': 'New'})
        self.assertTrue(all(r['description'] == '' for r in records.values()))
        cards = {c['href']: c['visible_lines'] for c in data['product']['related_cards']}
        self.assertEqual(set(cards), {f'/product/{code}' for code in records})
        self.assertEqual(records['909']['price'], 0)
        self.assertIn('Ask for price', cards['/product/909'])
        self.assertIn('IPHONE 11 128GB USED', cards['/product/909'])


class AuditAndRegistryTripwireTests(unittest.TestCase):
    def test_the_audit_still_leaves_every_amhaz_identity_unresolved(self):
        paths, records = audit_records()
        self.assertTrue(paths)
        self.assertEqual([r['retailer_id'] for _, r in records], [amhaz.RETAILER_ID])
        for path, record in records:
            with self.subTest(audit=path, retailer=record['retailer_id']):
                self.assertEqual(record['identity_status'], amhaz.IDENTITY_STATUS, REPLACE)
                self.assertIsNone(record['canonical_seller'], REPLACE)
                self.assertEqual(record['qualifying_offer_status'], 'not_established')

    def test_the_seed_holds_one_candidate_with_the_profile_origin_and_no_aliases(self):
        seeds = seed_records()
        self.assertEqual([s['retailer_id'] for s in seeds], [amhaz.RETAILER_ID])
        seed = seeds[0]
        self.assertEqual(seed['origin'], amhaz.CANDIDATE_ORIGIN)
        self.assertEqual(seed.get('aliases', []), [])
        self.assertEqual(seed.get('related_origins', []), [])
        self.assertIsNone(seed.get('capability'))
        self.assertEqual(set(seed['categories']), set(amhaz.PROFILE.category_leads))

    def test_no_source_registration_or_dispatch_reaches_the_candidate(self):
        registry = json.loads((ROOT / 'config/sources.json').read_text(encoding='utf-8'))
        for source in registry['sources']:
            with self.subTest(source=source['id']):
                self.assertNotIn('amhaz', source['id'].casefold(), REPLACE)
                self.assertNotIn('amhaz', source['origin'].casefold(), REPLACE)
        self.assertNotIn(amhaz.RETAILER_ID, registry['live_release']['admitted_source_ids'])
        with self.assertRaises(PermissionError):
            get_active_source(amhaz.RETAILER_ID)
        self.assertNotIn(amhaz.RETAILER_ID, adapters.ADAPTERS)
        self.assertNotIn(amhaz.RETAILER_ID, adapters.CATALOGS)
        with self.assertRaises(KeyError):
            adapters.search_catalog(ServingFetch(), amhaz.RETAILER_ID, category='phone', search='iPhone 17')


class DirectoryLeadTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.url = os.environ.get('TEST_DATABASE_URL') or f"sqlite:///{Path(directory) / 'amhaz.db'}"
        migrate(self.url, 'base')
        migrate(self.url)
        self.storage = Storage(self.url, Path(directory) / 'evidence')
        self.addCleanup(migrate, self.url, 'base')
        self.addCleanup(self.storage.engine.dispose)
        # The real registry: the candidate must not become configured through it.
        self.directory = Directory(self.storage, registry_path=ROOT / 'config/sources.json')
        import_audit(self.directory)

    def test_candidate_is_an_unconfigured_lead_only(self):
        record = self.directory.get(amhaz.RETAILER_ID)
        self.assertEqual((record.seller_identity, record.seller_id, record.marketplace_id, record.capabilities),
                         ('unresolved', None, None, ()))
        self.assertTrue(all(not c.retrieval_tested and not c.qualifying_offer for c in record.coverage))
        for category in amhaz.PROFILE.category_leads:
            with self.subTest(category=category):
                # limit=20 is the query maximum; the candidate must still be among the leads.
                hits = {h.record.entry.retailer_id: h for h in self.directory.select(
                    DirectoryQuery(category=category, include_unconfigured=True, limit=20))}
                self.assertIn(amhaz.RETAILER_ID, hits, 'candidate missing from the bounded lead list')
                hit = hits[amhaz.RETAILER_ID]
                self.assertEqual((hit.configured, hit.permitted_urls), (False, ()))
                self.assertIn('technical configuration gap; lead only', hit.reasons)
                configured = self.directory.select(DirectoryQuery(category=category))
                self.assertNotIn(amhaz.RETAILER_ID, {h.record.entry.retailer_id for h in configured})

    def test_similar_names_do_not_resolve_to_the_candidate(self):
        for name in ('Amhaz', 'amhaz', 'Amhaz Official', 'Amhaz Mobile', 'Amhaz Original', 'Amaz'):
            with self.subTest(name=name):
                self.assertIsNone(self.directory.resolve_alias(name))
        self.assertEqual(self.directory.resolve_alias(amhaz.RETAILER_ID).entry.retailer_id, amhaz.RETAILER_ID)


if __name__ == '__main__':
    unittest.main()
