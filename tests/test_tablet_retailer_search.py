"""A category-scoped tablet miss continues to a second admitted merchant."""
import json

import httpx2

from app import contracts_v2 as v2
from app.directory import import_audit
from app.domestic_buyer_sources import DomesticBuyerSources
from app.source_registry import DEFAULT_REGISTRY
from app.sources import SourceGate, SourceLimiter
from test_buyer_flow import FlowCase
from test_buyer_interpretation import form


class TabletRetailerSearchTests(FlowCase):
    def test_second_merchant_proves_capacity_on_its_selected_page(self):
        import_audit(self.directory)
        request = form('Find a tablet with at least 64 GB storage.', 'tablet')
        page = 'https://beytech.com.lb/product/studio-slate/'
        results = [dict(id=32, parent=0, type='simple', slug='studio-slate', permalink=page,
                        categories=[dict(slug='tablets')])]
        product = {'@type':'Product', 'url':page, 'productID':'SL', 'sku':'SL-64',
                   'model':'Slate', 'name':'Studio Slate tablet with folding stand',
                   'brand':{'name':'Studio'}, 'category':'tablet',
                   'additionalProperty':[{'@type':'PropertyValue', 'name':'Storage', 'value':'64 GB'}],
                   'offers':{'@type':'Offer', 'url':page, 'price':'89', 'priceCurrency':'USD',
                             'seller':{'name':'Beytech'}, 'availability':'https://schema.org/InStock'}}
        def reply(http):
            if str(http.url) == page:
                body = '<script type="application/ld+json">' + json.dumps(product) + '</script>'
            elif http.url.host == 'beytech.com.lb':
                body = json.dumps(results)
            else:
                body = '<html>No tablets found</html>'
            raw = body.encode()
            return httpx2.Response(200, headers={'content-type':'application/json' if http.url.path.endswith('/products') else 'text/html', 'content-length':str(len(raw))},
                                   stream=httpx2.ByteStream(raw))

        gate = SourceGate._for_tests(DEFAULT_REGISTRY, resolver=lambda *_: ['1.1.1.1'],
                                     limiter=SourceLimiter(), transport=httpx2.MockTransport(reply))
        sources = DomesticBuyerSources(self.storage, gate, self.directory)
        job = self.ledger.open_job('tablet-search')
        searches = sources.search_listings(request, job, room=lambda: 90)
        first, second = next(searches), next(searches)
        self.assertEqual((first['source_id'], first['links']), ('technodel', ()))
        self.assertEqual((second['source_id'], second['links']), ('beytech', (page,)))
        self.assertEqual(first['query'], 'tablet 64GB')
        self.assertEqual(second['query'], first['query'])
        run = v2.RunReference(run_id=job.run_id, request_id=request.request_id, request_revision=1, run_revision=1)
        read = sources.read(request, page, job, run, room=lambda: 90)
        self.assertEqual(read.status, 'ok', read.reason)
        offer, = read.offers
        self.assertEqual((offer.variant.product.model, offer.source_variant_id, offer.variant.specs.storage_gb.value),
                         ('Slate', 'SL-64', 64))
