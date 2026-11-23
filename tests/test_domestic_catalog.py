"""Domestic listings, permission boundaries and price history from bounded synthetic captures."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

import httpx2
from sqlalchemy import select

from app.accounting import Ledger
from app.bootstrap import Manifest, Seed, collect
from app.contracts import SourcePolicy
from app.contracts_v2 import DeliveryDestination, Evidence, RunReference
from app.corpus import Corpus
from app.corpus_contracts import DocumentCapture, Extraction, FieldObservation, ResourceKey
from app.directory import Directory, DirectoryRecord
from app.domestic_catalog import DomesticCatalog, exact_variant_key, extract_listing, known
from app.embeddings import EmbeddingRoute, EmbeddingService
from app.retrieval import DIMENSIONS, ROUTED_MODEL, EvidenceQuery, EvidenceRetrieval
from app.sources import FetchOutcome
from app.storage import Storage, corpus_observations
import test_corpus as fixtures
from test_corpus import START


class DomesticListingTests(TestCase):
    def setUp(self):
        fixture = fixtures.CorpusTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.storage, self.corpus = fixture.storage, fixture.corpus
        self.database_url = fixture.url
        self.registry = self.storage.snapshots.root.parent/'catalog-registry.json'
        self.directory = Directory(self.storage,registry_path=self.registry)
        self.catalog = DomesticCatalog(self.storage,registry_path=self.registry)
        self.policies, self.pages, self.seeds = [], {}, []
        self.ledger = Ledger(self.storage,'domestic-listing-test',fixture=True)
        self.ledger.provision()
        self.gate = Mock(_registry_path=self.registry)
        self.gate.fetch.side_effect = self.fetch
        self.clock = START
        self.requests = []
        self.route = EmbeddingRoute.configured(rate=Decimal('.02'),environ={'OPENROUTER_API_KEY':'test'},
                                               transport=httpx2.MockTransport(self.embed_reply))
        self.add_store('one')
        self.add_store('two')
        self.template = Manifest.model_validate_json(Path('config/domestic-bootstrap-validation.json').read_bytes())

    def add_store(self, name, *, canonical=None):
        origin = f'https://{name}.example'
        sku = f'sku-{name}'
        url = origin+'/devices/selected'
        policy = SourcePolicy.model_validate(dict(id=name,name=name,origin=origin,status='active',
            reviewed_at=START.date(),admission=dict(basis='Fixture',evidence=[url],reviewed_at=START,scope='Exact fixture URL'),
            allowed_endpoints=[url],limits=dict(requests_per_minute=6,concurrency=1,max_requests_per_run=4)))
        self.storage.save_source(policy)
        self.policies.append(policy.model_dump(mode='json'))
        self.registry.write_text(json.dumps({'sources':self.policies}),encoding='utf-8')
        record = DirectoryRecord(entry=dict(retailer_id=name,name=name,origin=origin,markets=['LB'],categories=['phone'],
            aliases=[name+' ltd'],discovery_status='discovered'),source_country='LB',role='domestic',
            source_id=name,seller_identity='retailer',seller_id=canonical or name,updated_at=START,
            product_urls=(url,),background_collection=True)
        self.directory.upsert(record)
        self.pages[name] = {'@type':'Product','@id':url+'#product','url':url,'name':'Phone X',
            'brand':{'@type':'Brand','name':'Acme'},'mpn':'PX-128-BLK','sku':sku,'color':'Black',
            'additionalProperty':[{'name':'Storage','value':'128 GB'}],
            'offers':{'@type':'Offer','price':'100.00','priceCurrency':'USD','seller':{'name':name+' ltd'},
                'availability':'https://schema.org/InStock','shippingDetails':{
                    'shippingOrigin':{'addressCountry':'LB'}}}}
        self.seeds.append(Seed(source_id=name,url=url,category='phone',content='listing',format='html',
            product_id='p-'+name,sku=sku,listing={'retailer_id':name}))

    def fetch(self, budget, source, url, **kwargs):
        hold = budget.reserve_call(source,1)
        if not hold:
            raise AssertionError('Unexpected fixture source denial')
        hold.release()
        body = self.body(source)
        return FetchOutcome(source,url,(url,),1,1,final_url=url,body=body,observed_at=self.clock,
                            response_sha256=sha256(body).hexdigest())

    def body(self,source):
        return ('<script type="application/ld+json">'+json.dumps(self.pages[source])+'</script>').encode()

    def embed_reply(self,req):
        self.requests.append(req)
        data = json.loads(req.content)
        return httpx2.Response(200,json={'model':ROUTED_MODEL,
            'usage':{'total_tokens':len(data['input']),'cost':0.0000001,'is_byok':False},
            'data':[{'index':i,'embedding':[1.0]+[0.0]*(DIMENSIONS-1)} for i in range(len(data['input']))]})

    def collect(self,name):
        manifest = self.template.model_copy(update={'manifest_id':name,'seeds':tuple(self.seeds),
            'limits':self.template.limits.model_copy(update={'max_paid_calls':10,'max_spend_usd':Decimal('.01')})})
        return collect(manifest,ledger=self.ledger,corpus=self.corpus,gate=self.gate,
                       embedder=self.route.provider,rate=self.route.rate)

    def extract(self,seed, *, destination=None):
        profile = self.catalog.seed_profile(seed)
        key = ResourceKey(source_id=seed.source_id,url=seed.url,product_id=seed.product_id,variant_id=seed.sku,
                          route_id='lb-delivery',representation='html')
        body = self.body(seed.source_id)
        capture = DocumentCapture(key=key,body=body,relevant_content=body,observed_at=self.clock,checked_at=self.clock,
            origin='runtime',final_url=seed.url,visibility='public',processing=self.template.processing)
        run = RunReference(run_id='run',run_revision=1,request_id='req',request_revision=1)
        extracted = extract_listing(capture,profile,run=run,destination=destination)
        return {i.evidence[0].field_path:i for i in extracted.observations}

    def test_persistent_pair_routed_search_idempotence_and_price_history(self):
        report = self.collect('initial')
        self.assertEqual({v['status'] for v in report['pages'].values()},{'indexed'},report['pages'])
        self.assertEqual((report['all']['fetch'],report['all']['paid']),(2,2))
        self.assertEqual(self.collect('initial')['pages'],report['pages'])
        self.assertEqual(len(self.requests),2)
        reopened = Storage(self.database_url,self.storage.snapshots.root)
        self.addCleanup(reopened.engine.dispose)
        lookup = DomesticCatalog(reopened,registry_path=self.registry)
        items = lookup.lookup(category='phone',text='Acme phone 128')
        self.assertEqual(len(items),2)
        self.assertEqual({i.seller_id for i in items},{'one','two'})
        self.assertEqual(len({i.variant_key for i in items}),1)
        self.assertEqual(lookup.lookup(category='laptop'),())
        totals = lookup.coverage(as_of=START)['totals']
        self.assertEqual(totals['recent_priced_variants'],1)
        self.assertEqual(totals['domestic_variants_with_two_sellers'],1)
        for i in items:
            self.assertTrue(i.can_revisit)
            self.assertFalse(i.can_enumerate)
            self.assertEqual(i.observations['route.origin_country'].evidence[0].scope,'route')
            self.assertIn('catalog.delivery_country',i.summary()['unknown'])
            self.assertIn('costs.order.shipping',i.summary()['unknown'])
            self.assertIsNone(known(i.observations,'purchasable'))
        item = items[0]
        query = EvidenceQuery(text='phone',source_ids=[item.source_id],scope='variant',
            field_paths=['variant.product.model'],context=dict(now=START,product_id=item.product_id,variant_id=item.sku),
            **{k:getattr(item.processing,k) for k in ('corpus_version','index_version','chunk_version')})
        corpus = Corpus(reopened)
        retrieval = EvidenceRetrieval(corpus)
        self.assertTrue(retrieval.has_routed_vectors(query))
        job = self.ledger.open_job('domestic-query','buyer')
        service = EmbeddingService(corpus,self.route.provider,usd_per_million=self.route.rate,job=job)
        vector, = service.embed([query.text],owner_id=job.run_id,purpose='query',index_version=query.index_version,
                                max_spend_usd=Decimal('.01'))
        hit, = retrieval.retrieve(query,vector=vector,vector_model=ROUTED_MODEL)
        self.assertIn('vector',hit.channels)
        self.assertEqual(hit.passage.evidence.origin,'bootstrap')
        self.assertEqual({str(r.url) for r in self.requests},{'https://openrouter.ai/api/v1/embeddings'})
        previous = self.catalog.lookup(category='phone',listing_url=self.seeds[0].url)[0]
        self.clock += timedelta(hours=1)
        self.pages['one']['offers']['price'] = '125.00'
        newest = self.collect('new-prices')
        self.assertEqual(newest['all']['paid'],0)
        self.assertEqual(len(self.requests),3)
        item, = lookup.lookup(category='phone',listing_url=self.seeds[0].url)
        self.assertEqual(item.observations['price'].evidence[0].observed_at,self.clock)
        self.assertEqual(item.observations['variant.product.model'].evidence[0].observed_at,START)
        self.assertEqual(self.corpus.passages(previous.document_id),self.corpus.passages(item.document_id))
        self.assertNotEqual(previous.document_id,item.document_id)
        history=lookup.price_history(item)
        self.assertEqual([p.evidence[0].observed_at for p in history],[self.clock,START])
        with reopened.engine.connect() as db:
            prices = db.execute(select(corpus_observations.c.price_amount).where(
                corpus_observations.c.field_path=='price',corpus_observations.c.variant_id==item.sku)
                .order_by(corpus_observations.c.observed_at)).scalars().all()
        self.assertEqual(prices,[Decimal('100'),Decimal('125')])
        self.assertEqual(lookup.coverage(as_of=START+timedelta(days=9))['totals']['recent_priced_variants'],0)

    def test_distinct_sellers_exact_variants_and_separate_origin(self):
        self.add_store('alias',canonical='one')
        del self.pages['two']['offers']['shippingDetails']['shippingOrigin']
        self.pages['two']['offers']['shippingDetails']['shippingDestination'] = {'addressCountry':'LB'}
        self.collect('missing-origin')
        report = self.catalog.coverage(as_of=START)
        self.assertEqual(report['totals']['listings'],3)
        self.assertEqual(report['totals']['recent_priced_variants_with_two_sellers'],1)
        self.assertEqual(report['totals']['domestic_variants_with_two_sellers'],0)
        two, = self.catalog.lookup(category='phone',listing_url=self.seeds[1].url)
        self.assertEqual(known(two.observations,'catalog.delivery_country'),'LB')
        self.assertIsNone(known(two.observations,'route.origin_country'))
        self.clock += timedelta(hours=1)
        self.pages['two']['offers']['shippingDetails']['shippingOrigin'] = {'addressCountry':'LB'}
        self.pages['two']['color'] = 'White'
        self.collect('conflicting-config')
        self.assertEqual(self.catalog.coverage(as_of=self.clock)['totals']['domestic_variants_with_two_sellers'],0)
        self.assertEqual(self.catalog.coverage(as_of=START)['totals']['recent_priced_variants'],1)
        del self.pages['one']['mpn']
        self.assertIsNone(exact_variant_key('phone',self.extract(self.seeds[0])))
        self.pages['one']['gtin13'] = '4006381333931'
        self.assertEqual(exact_variant_key('phone',self.extract(self.seeds[0])),'phone:gtin:04006381333931')

    def test_selected_offer_and_exact_destination_evidence_only(self):
        product = self.pages['one']
        offer = product['offers']
        shipping = offer['shippingDetails']
        shipping['shippingDestination']={'addressCountry':'LB','addressLocality':'Beirut'}
        shipping['shippingRate']={'value':'0','currency':'USD'}
        offer['priceSpecification']={'valueAddedTaxIncluded':True}
        product['additionalProperty'].append({'name':'Purchasable','value':True})
        seed=self.seeds[0]
        fields=self.extract(seed,destination=DeliveryDestination(city='Beirut'))
        self.assertTrue(known(fields,'route.item_eligible'))
        self.assertTrue(known(fields,'purchasable'))
        self.assertEqual(known(fields,'costs.order.tax'),'included')
        self.assertEqual(known(fields,'costs.order.shipping'),{'amount':'0','currency':'USD'})
        self.assertEqual(fields['costs.order.shipping'].evidence[0].route_id,'lb-delivery')
        fields=self.extract(seed,destination=DeliveryDestination(city='Tripoli'))
        self.assertIsNone(known(fields,'route.item_eligible'))
        self.assertIsNone(known(fields,'costs.order.shipping'))
        shipping['shippingDestination']['postalCode']='1000'
        self.assertIsNone(known(self.extract(seed,destination=DeliveryDestination(city='Beirut')),'catalog.delivery_country'))
        for field,value in [('offeredBy',{'name':'Marketplace Seller'}),('url','https://one.example/another'),
                            ('priceValidUntil','2020-01-01'), ('eligibleCustomerType','members')]:
            with self.subTest(field=field):
                offer[field]=value
                self.assertNotIn('price',self.extract(seed))
                del offer[field]
        product['offers']=[offer,deepcopy(offer)]
        self.assertNotIn('price',self.extract(seed))
        product['offers']=offer
        product['@id']='https://one.example/other#product'
        self.assertNotIn('price',self.extract(seed))
        product['@id']='#product'
        product['sku']='other-configuration'
        self.assertEqual(self.extract(seed),{})

    def test_companion_receipt_is_bound_to_its_own_page_and_subject(self):
        seed = self.seeds[1]
        collection_url='https://two.example/collection'
        self.policies[1]['allowed_endpoints'].append(collection_url)
        self.registry.write_text(json.dumps({'sources':self.policies}),encoding='utf-8')
        self.storage.save_source(SourcePolicy.model_validate(self.policies[1]))
        self.pages['two']['offers'].pop('shippingDetails')
        self.pages['two']['collectionFixture']='Membership and dispatch from Lebanon'
        companion = Seed(source_id=seed.source_id,url=collection_url,product_id=seed.product_id,sku=seed.sku,
            category='phone',content='listing',format='html',dispatch_for=dict(retailer_id='two',listing_url=seed.url))
        self.seeds.append(companion)

        def membership(capture,profile):
            origin='Membership and dispatch from Lebanon'
            if origin.encode() not in capture.body:
                return Extraction()
            receipt=Evidence(evidence_id=sha256(capture.body).hexdigest()+':dispatch',field_path='route.origin_country',
                scope='route',route_id='catalog-dispatch',source_id=seed.source_id,product_id=seed.product_id,
                variant_id=seed.sku,origin_url=capture.key.url,final_url=capture.final_url,origin=capture.origin,
                excerpt=origin,observed_at=capture.observed_at,response_sha256=sha256(capture.body).hexdigest(),
                adapter_version=capture.processing.extraction_version,corpus_version=capture.processing.corpus_version)
            return Extraction(observations=(FieldObservation(fact=dict(state='known',value='LB',evidence_ids=[receipt.evidence_id]),
                evidence=(receipt,)),))

        with patch('app.domestic_retailer_profiles.extract_dispatch_membership',side_effect=membership):
            self.collect('membership')
            item, = self.catalog.lookup(category='phone',listing_url=seed.url)
            fact=item.observations['route.origin_country']
            self.assertEqual(fact.fact.value,'LB')
            self.assertEqual(str(fact.evidence[0].origin_url),collection_url)
            self.assertEqual(fact.evidence[0].variant_id,seed.sku)
            self.assertEqual(self.catalog.coverage(as_of=START)['totals']['domestic_variants_with_two_sellers'],1)
            # A collection membership is revoked by a later page without that SKU/claim.
            self.clock += timedelta(hours=1)
            self.pages['two'].pop('collectionFixture')
            self.collect('membership-missing')
            item, = self.catalog.lookup(category='phone',listing_url=seed.url)
            self.assertNotIn('route.origin_country',item.observations)
            self.assertEqual(self.catalog.coverage(as_of=self.clock)['totals']['domestic_variants_with_two_sellers'],0)
            self.assertEqual(self.catalog.coverage(as_of=START)['totals']['domestic_variants_with_two_sellers'],1)

    def test_product_permission_and_catalog_permission_can_change_independently(self):
        seed=self.seeds[0]
        read_rule=dict(capability_id='read-item',version='1',host='one.example',operation='product',
                       expect='html',path=['devices','selected'])
        enumerate_rule=dict(capability_id='list-phones',version='1',host='one.example',operation='catalog',
                            expect='html',path=['phones'])
        self.policies[0]['allowed_endpoints']=[]
        self.policies[0]['endpoint_capabilities']=[read_rule]
        self.registry.write_text(json.dumps({'sources':self.policies}),encoding='utf-8')
        profile=self.catalog.seed_profile(seed)
        self.assertEqual(profile.capability.capability_id,'read-item')
        self.assertTrue(profile.can_revisit)
        self.assertFalse(profile.can_enumerate)
        self.collect('scoped')
        self.assertEqual(self.gate.fetch.call_args_list[0].kwargs['capability'],profile.capability)
        self.policies[0]['endpoint_capabilities']=[enumerate_rule]
        self.registry.write_text(json.dumps({'sources':self.policies}),encoding='utf-8')
        item,=self.catalog.lookup(category='phone',listing_url=seed.url)
        self.assertFalse(item.can_revisit)
        self.assertTrue(item.can_enumerate)
        self.assertEqual([r.capability_id for r in item.enumeration_capabilities],['list-phones'])
        record=self.directory.get('one').model_copy(update={'seller_id':'replacement','updated_at':START+timedelta(hours=1)})
        self.directory.upsert(record)
        self.assertEqual(self.catalog.lookup(category='phone',listing_url=seed.url),())

    def test_revocation_and_unknown_seller_fail_closed(self):
        self.collect('before-revocation')
        self.policies[0]['allowed_endpoints']=['https://one.example/another']
        self.policies[1]['status']='paused'
        self.registry.write_text(json.dumps({'sources':self.policies}),encoding='utf-8')
        item, = self.catalog.lookup(category='phone')
        self.assertFalse(item.can_revisit)
        self.assertEqual(self.catalog.coverage(as_of=START)['totals']['recent_priced_variants'],0)
        report=self.collect('revoked')
        self.assertEqual(self.gate.fetch.call_count,2)
        self.assertEqual({v['status'] for v in report['pages'].values()},{'failed'})
        historic=self.template.seeds[0]
        self.assertNotIn('listing',historic.model_dump())
        self.assertNotIn('dispatch_for',historic.model_dump())
