from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from pydantic import ValidationError

from app.contracts import SourcePolicy
from app.contracts_v2 import VerifiedOffer, RunReference
from app.corpus_contracts import DocumentCapture, ResourceKey
from app.extraction import PageProfile, extract_structured, extract_page, verified_price_order
from app.page_extraction import read_page
from app.accounting import Ledger
from app.sources import FetchOutcome
import test_corpus as fixtures
from test_corpus import START

ROOT = Path(__file__).resolve().parents[1]


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        fixtures.CorpusTests.setUp(self)

    def capture(self, source='pcandparts', *, body=None, origin='bootstrap'):
        host = 'https://pcandparts.com' if source=='pcandparts' else 'https://abedtahan.com'
        url = host + '/product/synthetic-fixture'
        policy = self.storage.load_source(fixtures.SOURCE).model_dump(mode='json')
        policy.update(id=source,origin=host,allowed_endpoints=[url])
        self.storage.save_source(SourcePolicy.model_validate(policy))
        body = body or (ROOT/'tests/fixtures/shared-extraction'/f'{source}.html').read_bytes()
        return DocumentCapture(key=ResourceKey(source_id=source,url=url,representation='html',
            product_id='synthetic-product',variant_id='fixture-16-512'),final_url=url,visibility='public',
            origin=origin,checked_at=START,observed_at=START,body=body,relevant_content=body,
            processing=dict(corpus_version='corpus-1',index_version='index-1',extraction_version='structured-1',chunk_version='chunks-1'))

    def profile(self, source='pcandparts'):
        return PageProfile(source,'https://pcandparts.com' if source=='pcandparts' else 'https://abedtahan.com',
                           sku='fixture-16-512',policy_element_id='shipping-policy')

    def fields(self, extraction):
        return {o.evidence[0].field_path:o for o in extraction.observations}

    def test_two_inventory_retailer_profiles_share_extractor_and_corpus(self):
        for source,price in (('pcandparts','900.25'),('abed_tahan','920.50')):
            with self.subTest(source=source):
                capture,profile = self.capture(source),self.profile(source)
                extracted = extract_page(capture,profile)
                fields = self.fields(extracted)
                self.assertEqual(fields['price'].fact.value,{'amount':price,'currency':'USD'})
                self.assertTrue(fields['stock'].fact.value)
                self.assertEqual(fields['stock'].evidence[0].scope,'variant')
                self.assertIsNone(fields['stock'].evidence[0].branch_id)
                self.assertEqual(fields['variant.specs.ram_gb'].fact.value,16)
                self.assertEqual(fields['variant.specs.storage_gb'].fact.value,512)
                self.assertIn('policy.shipping',fields)
                self.assertNotIn('purchasable',fields)
                stored = self.corpus.ingest(capture,lambda c:extract_page(c,profile))
                self.assertEqual(len(self.corpus.observations(stored.document_id)),len(fields))
                for observation in fields.values():
                    for receipt in observation.evidence:
                        self.assertIn(receipt.excerpt,capture.body.decode())

    def test_conflicting_price_and_stock_remain_conflicts(self):
        capture = self.capture()
        original = capture.body.decode()
        second = original.replace('900.25','850.25').replace('InStock','OutOfStock')
        capture = capture.model_copy(update={'body':(original+second).encode()})
        fields = self.fields(extract_structured(capture,self.profile()))
        self.assertEqual(fields['price'].fact.state,'conflict')
        self.assertEqual(fields['stock'].fact.state,'conflict')

    def test_exact_variant_missing_price_unknown_stock_and_aggregate_offer_stay_gaps(self):
        capture = self.capture('abed_tahan')
        wrong = PageProfile('abed_tahan','https://abedtahan.com',sku='fixture-other')
        with self.assertRaises(ValueError):
            extract_structured(capture,wrong)
        body = capture.body.replace(b'"920.50"',b'null').replace(b'InStock',b'PreOrder')
        fields = self.fields(extract_structured(capture.model_copy(update={'body':body}),self.profile('abed_tahan')))
        self.assertNotIn('price',fields)
        self.assertNotIn('stock',fields)
        aggregate = self.capture().body.replace(b'"@type":"Offer"',b'"@type":"AggregateOffer"')
        self.assertNotIn('price',self.fields(extract_structured(self.capture(body=aggregate),self.profile())))

    def test_page_injection_cannot_change_constraints_or_invoke_tools(self):
        capture = self.capture('abed_tahan')
        extracted = extract_structured(capture,self.profile('abed_tahan'))
        fields = self.fields(extracted)
        self.assertEqual(fields['price'].fact.value['amount'],'920.50')
        self.assertFalse(any('fees zero' in p.text for p in extracted.passages))
        # The extraction API has no provider, job, tool registry or buyer mutation port.
        self.assertEqual(capture.key.variant_id,'fixture-16-512')

    def test_dedicated_parser_precedes_shared_metadata(self):
        capture = self.capture()
        expected = extract_structured(capture,self.profile())
        dedicated = Mock(return_value=expected)
        self.assertEqual(extract_page(capture,self.profile(),dedicated=dedicated),expected)
        dedicated.assert_called_once_with(capture)

    def test_bad_provenance_and_branch_binding_are_rejected(self):
        capture = self.capture()
        changed = DocumentCapture.model_validate(capture.model_dump() | {'final_url':'https://evil.example/p'})
        with self.assertRaises(ValueError):
            extract_structured(changed,self.profile())
        branch = capture.key.model_copy(update={'branch_id':'branch-1'})
        with self.assertRaises(ValueError):
            extract_structured(capture.model_copy(update={'key':branch}),self.profile())

    def verified(self,source,price):
        raw = json.loads((ROOT/'tests/fixtures/v2/workspace.json').read_text())['current']['recommendations'][0]['primary']
        # Supplementary synthetic evidence is deliberately independent of shared
        # metadata: purchasability, tax, shipping and route eligibility still need it.
        offer = raw['offer']
        offer.update(offer_id=source,retailer_id=source,source_id=source,source_product_id='synthetic-product',
                     source_variant_id='fixture-16-512')
        offer['seller']['seller_id']=source
        capture = self.capture(source,origin='runtime')
        offer['listing_url']=str(capture.key.url)
        for receipt in offer['evidence']:
            receipt.update(source_id=source,product_id='synthetic-product',
                           variant_id='fixture-16-512',origin_url=str(capture.key.url),final_url=str(capture.final_url))
        run = RunReference.model_validate(raw['verification']['run'])
        fields = self.fields(extract_structured(capture,self.profile(source),run=run))
        replacing = ('price','stock','condition')
        offer['evidence']=[r for r in offer['evidence'] if r['field_path'] not in replacing]
        for field in replacing:
            offer[field]=fields[field].fact.model_dump(mode='json')
            offer['evidence'].extend(r.model_dump(mode='json') for r in fields[field].evidence)
        additional = sum(Decimal(c['amount']['amount']) for c in offer['costs'] if c.get('amount'))
        raw['cost']['total']['amount']=str(Decimal(price)+additional)
        return VerifiedOffer.model_validate(raw)

    def test_verified_shared_offers_compete_without_parser_preference(self):
        low = self.verified('pcandparts','900.25')
        high = self.verified('abed_tahan','920.50')
        adapter = deepcopy(high.model_dump(mode='json'))
        adapter['offer']['offer_id']='adapter-control'
        for receipt in adapter['offer']['evidence']:
            receipt['adapter_version']='dedicated-parser-1'
        adapter = VerifiedOffer.model_validate(adapter)
        ranked = verified_price_order((adapter,high,low))
        self.assertEqual(ranked[0].offer.retailer_id,'pcandparts')
        self.assertEqual(ranked[1].cost.total.amount,ranked[2].cost.total.amount)
        self.assertEqual(ranked[1].offer.offer_id,'abed_tahan')

    def test_unsupported_fields_cannot_create_a_verified_offer(self):
        raw = self.verified('pcandparts','900.25').model_dump(mode='json')
        raw['offer']['purchasable']={'state':'unknown','reason':'not checked'}
        with self.assertRaises(ValidationError):
            VerifiedOffer.model_validate(raw)

    def test_html_shell_uses_one_explicit_fallback_and_meters_local_work(self):
        capture = self.capture()
        ledger = Ledger(self.storage,'page-fixture',fixture=True)
        ledger.provision()
        job = ledger.open_job('page','bootstrap')
        url = str(capture.key.url)
        gate = Mock(_registry_path='unused-by-fake-gate')
        gate.fetch.return_value = FetchOutcome('pcandparts',url,(url,),1,1,final_url=url,
            status_code=200,content_type='text/html',body=b'<html>loading...</html>',observed_at=START)
        fallback = Mock()
        fallback.scrape.return_value.outcome = FetchOutcome('pcandparts',url,(url,),1,1,final_url=url,
            status_code=200,content_type='text/html',body=capture.body,observed_at=START)
        result = read_page(gate,job,capture.key,self.profile(),capture.processing,origin='bootstrap',fallback=fallback)
        self.assertEqual(result.method,'managed-shared')
        self.assertEqual(self.fields(result.extraction)['price'].fact.value['amount'],'900.25')
        fallback.scrape.assert_called_once()
        self.assertEqual(len(ledger.snapshot()['attempts']),2)  # two local extraction windows

    def test_duplicate_json_keys_cannot_silently_choose_a_price(self):
        capture = self.capture()
        capture = capture.model_copy(update={'body':capture.body.replace(b'"price":"900.25"',
                                                   b'"price":"1","price":"900.25"')})
        self.assertNotIn('price',self.fields(extract_structured(capture,self.profile())))
