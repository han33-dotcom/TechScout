"""Directory import and source-selection regressions using synthetic/local research."""
import asyncio
from datetime import timedelta
import json
from pathlib import Path
import unittest

from pydantic import ValidationError

from app.collection_contracts import RetailerDirectoryEntry
from app.directory import (
    CategoryCoverage, Directory, DirectoryPort, DirectoryQuery, DirectoryRecord,
    import_audit,
)
from app.source_capabilities import CapabilityRef
import test_corpus as fixtures
from test_corpus import SOURCE, START, URL


class DirectoryTests(unittest.TestCase):
    def setUp(self):
        fixtures.CorpusTests.setUp(self)
        self.registry = self.storage.snapshots.root.parent/'directory-sources.json'
        self.source = self.storage.load_source(SOURCE).model_dump(mode='json')
        self.registry.write_text(json.dumps({'sources':[self.source]}),encoding='utf-8')
        self.directory = Directory(self.storage,registry_path=self.registry)

    def record(self,retailer_id='offline',**changes):
        entry = RetailerDirectoryEntry(retailer_id=retailer_id,name='Offline Seller',origin='https://store.example',
            markets=('LB',),categories=('laptop',),aliases=('Offline Alias',),discovery_status='discovered')
        data = dict(entry=entry,updated_at=START,source_id=SOURCE,source_country='US',
            seller_id=retailer_id,seller_identity='retailer',product_urls=(URL,),background_collection=True,
            coverage=(CategoryCoverage(category='laptop',discovered=True,missing_fields=('gpu',)),))
        return DirectoryRecord(**(data|changes))

    def test_idempotent_upsert_stale_update_and_alias(self):
        record = self.record()
        self.assertTrue(self.directory.upsert(record))
        self.assertFalse(self.directory.upsert(record))
        self.assertFalse(self.directory.upsert(record.model_copy(update={'updated_at':START-timedelta(seconds=1)})))
        self.assertEqual(self.directory.resolve_alias('  OFFLINE   alias ').entry.retailer_id,'offline')
        self.assertEqual(asyncio.run(DirectoryPort(self.directory).get('offline')),record.entry)
        with self.assertRaisesRegex(ValueError,'Conflicting'):
            self.directory.upsert(record.model_copy(update={'role':'domestic'}))

    def test_category_market_and_foreign_source_origin_are_separate(self):
        self.directory.upsert(self.record())
        hit, = self.directory.select(DirectoryQuery(category='laptop'))
        self.assertTrue(hit.configured)
        self.assertEqual(hit.record.source_country,'US')
        self.assertEqual(hit.permitted_urls,(URL,))
        self.assertEqual(self.directory.select(DirectoryQuery(category='phone')),())
        with self.assertRaises(ValidationError):
            DirectoryQuery(category='laptop',purchase_markets=('US',))

    def test_disabled_unconfigured_and_wrong_capability_stay_leads(self):
        record = self.record()
        self.directory.upsert(record)
        self.source['status']='paused'
        self.registry.write_text(json.dumps({'sources':[self.source]}),encoding='utf-8')
        self.assertEqual(self.directory.select(DirectoryQuery(category='laptop')),())
        lead, = self.directory.select(DirectoryQuery(category='laptop',include_unconfigured=True))
        self.assertFalse(lead.configured)
        self.assertEqual(lead.permitted_urls,())
        self.source['status']='active'
        self.registry.write_text(json.dumps({'sources':[self.source]}),encoding='utf-8')
        self.directory.upsert(record.model_copy(update={'updated_at':START+timedelta(seconds=1),
            'capabilities':(CapabilityRef(capability_id='missing',version='v1'),)}))
        self.assertEqual(self.directory.select(DirectoryQuery(category='laptop')),())

    def test_marketplace_sellers_remain_distinct_and_alias_collision_is_explicit(self):
        for name in ('seller-a','seller-b'):
            self.directory.upsert(self.record(name,marketplace_id='marketplace',seller_identity='marketplace'))
        results = self.directory.select(DirectoryQuery(category='laptop',seek_competing_offers=True))
        self.assertEqual({r.record.seller_id for r in results},{'seller-a','seller-b'})
        self.assertTrue(all('investigate competing seller offers' in r.reasons for r in results))
        with self.assertRaisesRegex(ValueError,'Ambiguous'):
            self.directory.resolve_alias('Offline Alias')

    def test_capability_version_and_bounded_query(self):
        rule = dict(capability_id='products',version='1',host='store.example',operation='variant',
            expect='html',path=['products',{'kind':'token','max_length':48}],query=[])
        self.source.pop('allowed_endpoints')
        self.source['endpoint_capabilities']=[rule]
        self.registry.write_text(json.dumps({'sources':[self.source]}),encoding='utf-8')
        for identity in ('a','b','c'):
            self.directory.upsert(self.record(identity,capabilities=(CapabilityRef(capability_id='products',version='1'),)))
        results = self.directory.select(DirectoryQuery(category='laptop',candidate_limit=2,limit=1))
        self.assertEqual(len(results),1)
        self.assertTrue(results[0].configured)
        self.source['endpoint_capabilities'][0]['version']='2'
        self.registry.write_text(json.dumps({'sources':[self.source]}),encoding='utf-8')
        self.assertEqual(self.directory.select(DirectoryQuery(category='laptop')),())

    def test_selection_reasons_use_coverage_and_measured_evidence_not_buyer_trust(self):
        self.directory.upsert(self.record(price_bands=('budget',),configuration_gaps=('gpu',),
            useful_evidence_ids=('synthetic-spec-1',)))
        hit, = self.directory.select(DirectoryQuery(category='laptop',price_band='budget',configuration_gaps=('gpu',),purpose='collection',limit=1))
        self.assertIn('requested price-band coverage',hit.reasons)
        self.assertIn('configuration coverage gap',hit.reasons)
        self.assertIn('category retrieval coverage gap',hit.reasons)
        self.assertIn('1 useful evidence references recorded',hit.reasons)
        self.assertNotIn('trust', ' '.join(hit.reasons))
        with self.assertRaises(ValidationError):
            CategoryCoverage(category='laptop',discovered=True,qualifying_offer=True)
        tested = CategoryCoverage(category='laptop',discovered=True,retrieval_tested=True,
            checked_at=START,evidence_ids=('synthetic-attempt-1',))
        self.assertFalse(tested.qualifying_offer)

    def test_audit_import_preserves_dates_failures_and_no_qualification(self):
        self.assertEqual(import_audit(self.directory),20)
        self.assertEqual(import_audit(self.directory),0)
        mojitech = self.directory.get('mojitech')
        self.assertTrue(any(o.error for o in mojitech.observations))
        self.assertTrue(mojitech.policy_links)
        self.assertTrue(all(not c.qualifying_offer and not c.retrieval_tested for c in mojitech.coverage))
        self.assertEqual(self.directory.get('amhaz_official_candidate').seller_identity,'unresolved')
        self.assertEqual(self.directory.get('ayoub_computers').seller_identity,'marketplace')
        self.assertTrue(all(not h.configured for h in self.directory.select(DirectoryQuery(category='laptop',include_unconfigured=True))))

    def test_old_foreign_entries_are_excluded_from_research_and_collection(self):
        record = self.record()
        foreign = record.model_copy(update={'entry': record.entry.model_copy(update={'markets': ('US',)})})
        self.directory.upsert(foreign)
        for purpose in ('runtime', 'collection'):
            self.assertEqual(self.directory.select(DirectoryQuery(category='laptop', purpose=purpose)), ())
            with self.assertRaises(ValidationError):
                DirectoryQuery(category='laptop', purpose=purpose, purchase_markets=('US',))
