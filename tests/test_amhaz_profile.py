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
    audit = json.loads((ROOT / 'docs/retailer-audit-2026-11-16.json').read_text(encoding='utf-8'))
    return [r for r in audit['retailers'] if 'amhaz' in r['retailer_id'].casefold()]


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

    def test_reachable_priced_listing_with_stated_condition_is_still_no_candidate(self):
        # The capture shows the site is readable and states price, stock and condition for some
        # items; the block is about identity, not data.
        data = capture()
        stated = [r for r in data['related_records']
                  if r['price'] > 0 and r['attributes']['specs'].get('Condition') == 'New']
        self.assertTrue(stated)
        fetch = ServingFetch()
        self.assertBlocked(amhaz.PROFILE.search(fetch, category='phone', search='IPHONE 17'), fetch)

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


class AuditAndRegistryTripwireTests(unittest.TestCase):
    def test_the_audit_still_leaves_every_amhaz_identity_unresolved(self):
        records = audit_records()
        self.assertEqual([r['retailer_id'] for r in records], [amhaz.RETAILER_ID])
        for record in records:
            with self.subTest(retailer=record['retailer_id']):
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
                hits = {h.record.entry.retailer_id: h for h in
                        self.directory.select(DirectoryQuery(category=category, include_unconfigured=True))}
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
