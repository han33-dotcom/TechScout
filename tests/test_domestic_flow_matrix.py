"""Fresh API sessions using the seeded corpus, real gated readers and offline retailer responses."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import httpx2

from app.bootstrap import Manifest, Seed, extract_seed, seed_processing, seed_target
from app.contracts import SourcePolicy
from app.corpus_contracts import DocumentCapture
from app.directory import Directory, DirectoryRecord
from app.domestic_buyer_sources import DomesticBuyerSources
from app.sources import SourceGate, SourceLimiter
from test_buyer_flow import FlowCase
from test_domestic_buyer import Router, Search


class DomesticMatrix(FlowCase):
    def setUp(self):
        super().setUp()
        self.openrouter = Router()
        self.spec = dict(phone=('Storage','128 GB'), laptop=('RAM','16 GB'), tablet=('Storage','256 GB'),
                         monitor=('Refresh Rate','144 Hz'), audio=('Form Factor','headphones'))
        self.pages = {}
        registry = Path(self.traces).parent/'matrix-sources.json'
        directory = Directory(self.storage, registry_path=registry)
        now = datetime.now(timezone.utc) - timedelta(hours=1)
        sources = []
        seeds = []
        for shop in ('east','west'):
            origin = f'https://{shop}.example'
            urls = []
            for category,(field,value) in self.spec.items():
                url = origin+'/products/'+category
                urls.append(url)
                model = 'Demo ' + category
                product = {'@type':'Product','url':url,'name':model,'model':model,'brand':'Demo',
                    'productID':category,'sku':category+'-sku','mpn':'DEMO-101-'+category,'category':category,
                    'additionalProperty':[{'name':field,'value':value},{'name':'Purchasable','value':True}],
                    'offers':{'@type':'Offer','price':'120','priceCurrency':'USD','url':url,
                              'availability':'https://schema.org/InStock','itemCondition':'https://schema.org/NewCondition',
                              'priceSpecification':{'valueAddedTaxIncluded':True},'shippingDetails':{
                                'shippingOrigin':{'addressCountry':'LB'},'shippingDestination':{'addressCountry':'LB'},
                                'shippingRate':{'currency':'USD','value':'5'}}}}
                self.pages[url] = product
                seeds.append(Seed(source_id=shop,url=url,category=category,content='listing',format='html',
                                  product_id=category,sku=category+'-sku',listing={'retailer_id':shop}))
            policy = SourcePolicy.model_validate(dict(id=shop,name=shop,origin=origin,status='active',reviewed_at=now.date(),
                admission=dict(basis='Offline fixture',scope='Exact pages',evidence=urls,reviewed_at=now),
                allowed_endpoints=urls,limits=dict(requests_per_minute=120,concurrency=2,max_requests_per_run=6)))
            self.storage.save_source(policy)
            sources.append(policy.model_dump(mode='json'))
            directory.upsert(DirectoryRecord(entry=dict(retailer_id=shop,name=shop,origin=origin,markets=['LB'],
                categories=list(self.spec),discovery_status='discovered'),role='domestic',background_collection=True,
                updated_at=now,source_id=shop,seller_identity='retailer',seller_id=shop,source_country='LB',product_urls=urls))
        registry.write_text(json.dumps(dict(sources=sources, live_release=dict(admitted_source_ids=['east','west']))))
        manifest = Manifest.model_validate_json(Path('config/domestic-seller-coverage.json').read_bytes())
        self.initial_pages = []
        for seed in seeds:
            key, profile = seed_target(seed,self.corpus,registry)
            url = str(seed.url)
            body = self.body(url)
            capture = DocumentCapture(key=key,final_url=url,body=body,relevant_content=body,origin='bootstrap',
                        checked_at=now,observed_at=now,visibility='public',processing=seed_processing(manifest,seed))
            self.initial_pages.append((capture,seed,profile))
            self.pages[url]['offers']['price'] = '99' if seed.source_id == 'east' else '100'
        def reply(request):
            body = self.body(str(request.url))
            return httpx2.Response(200,headers={'content-type':'text/html','content-length':str(len(body))},
                                   stream=httpx2.ByteStream(body))
        gate = SourceGate._for_tests(registry, resolver=lambda *_:['1.1.1.1'], limiter=SourceLimiter(),
                                     transport=httpx2.MockTransport(reply))
        self.deps_for_matrix = self.deps()
        self.deps_for_matrix.domestic_sources = DomesticBuyerSources(self.storage,gate,directory)
        self.deps_for_matrix.research.workers.directory = directory
        self.search = Search(outage=True)
        self.deps_for_matrix.research.workers.domestic_tools = self.search

    def body(self, url):
        return ('<script type="application/ld+json">'+json.dumps(self.pages[url])+'</script>').encode()

    def test_both_entry_paths_for_all_five_categories_verify_current_source_responses(self):
        for capture, seed, profile in self.initial_pages:
            self.corpus.ingest(capture,lambda page: extract_seed(page,None,seed,profile,{},1))
        for category in self.spec:
            for kind in ('exact_model','needs'):
                with self.subTest(category=category,kind=kind):
                    intent = (dict(kind=kind,model='Demo '+category) if kind=='exact_model' else
                              dict(kind=kind,description='Find a '+category+' with a documented '+self.spec[category][0]))
                    # Each submission gets an independent buyer session and request.
                    self.client.cookies.clear()
                    session = self.client.get('/api/session').json()
                    self.headers['X-CSRF-Token'] = session['csrf_token']
                    _, run = self.browse(self.deps_for_matrix,request_id=category+'-'+kind,intent=intent,
                                         requirements=dict(category=category))
                    self.assertEqual(run.status,'complete',run.stop_reason)
                    self.assertEqual(run.coverage.qualifying,2)
                    group, = run.recommendations
                    self.assertEqual([str(v.cost.total.amount) for v in group.offers()],['104','105'])
                    self.assertTrue(all(v.verification.run == run.run for v in group.offers()))
                    trace = self.trace(run)
                    self.assertEqual(trace['limits']['concurrent_workers'],3)

    def test_cold_directory_yields_two_exact_offers_and_pickup_and_range_are_route_specific(self):
        _, cold = self.browse(self.deps_for_matrix,request_id='cold-phone',intent=dict(kind='exact_model',model='Demo phone'),
                              requirements=dict(category='phone',storage_gb=128))
        self.assertEqual((cold.coverage.qualifying,len(cold.recommendations[0].additional_sellers)),(2,1))
        self.assertEqual(self.search.calls,[])
        url = 'https://east.example/products/phone'
        node = self.pages[url]['offers']
        node.pop('shippingDetails')
        node.update(availableDeliveryMethod='https://schema.org/OnSitePickup',availableAtOrFrom={
            '@type':'Store','branchCode':'hamra','address':{'addressCountry':'LB','addressLocality':'Beirut'}})
        _, pickup = self.browse(self.deps_for_matrix,request_id='pickup-phone',intent=dict(kind='product_link',url=url),
                    requirements=dict(category='phone'),fulfillment=dict(mode='pickup',branch_id='hamra',destination=dict(city='Beirut')))
        picked = pickup.recommendations[0].primary
        self.assertEqual((picked.offer.route.branch_id,str(picked.cost.total.amount)),('hamra','99'))
        url = 'https://west.example/products/phone'
        self.pages[url]['offers']['shippingDetails']['shippingRate'] = dict(currency='USD',minValue='2',maxValue='5')
        _, estimated = self.browse(self.deps_for_matrix,request_id='range-phone',intent=dict(kind='product_link',url=url),
                                   requirements=dict(category='phone'))
        self.assertEqual(estimated.status,'abstained')
        self.assertEqual(estimated.uncertain[0].cost.status,'estimated')
