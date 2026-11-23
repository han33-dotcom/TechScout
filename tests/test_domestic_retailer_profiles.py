"""Source-specific variant and collection bindings with offline, synthetic Shopify pages."""
import json
from types import SimpleNamespace
import unittest

from app.corpus_contracts import DocumentCapture, Extraction
from app.domestic_retailer_profiles import BEIRUT_DISPATCH, COLLECTION, COLLECTION_DISPATCH, enrich_listing, extract_dispatch_membership


def listing(source='outgeeked', variant='10'):
    host = 'wincell.store' if source == 'wincell' else 'outgeeked.net'
    return SimpleNamespace(source_id=source, retailer_id=source, seller_id=source, seller_name=source.title(),
        seller_aliases=(), sku=variant, product_id='123', category='audio',
        listing_url=f'https://{host}/products/test-headphones?variant={variant}')


def capture(item, url, html):
    return DocumentCapture(key=dict(source_id=item.source_id, url=url, representation='html',
        product_id=item.product_id, variant_id=item.sku), final_url=url, body=html.encode(),
        relevant_content=html.encode(), origin='bootstrap', visibility='public',
        checked_at='2026-11-23T00:00:00Z', observed_at='2026-11-23T00:00:00Z',
        processing=dict(corpus_version='test', index_version='test', extraction_version='test', chunk_version='test'))


def member(base, sku, color, amount):
    return {'@type':'Product', 'name':f'Soundcore H30i - {color}', 'mpn':'local-code',
        'offers':{'@type':'Offer', 'url':f'{base}?variant={sku}', 'price':amount, 'priceCurrency':'USD',
                  'availability':'http://schema.org/InStock'}}


def product_facts(item, *, change=None):
    base = item.listing_url.split('?')[0]
    root = {'@type':'ProductGroup', 'brand':{'name':'Anker'}, 'url':base, 'productGroupID':item.product_id,
            'hasVariant':[member(base, '11', 'White', '19.50'), member(base, '10', 'Black', '42.25')]}
    if change:
        change(root)
    page = '<script type="application/ld+json">' + json.dumps(root) + '</script><footer>' + BEIRUT_DISPATCH + '</footer>'
    data = enrich_listing(capture(item, item.listing_url, page), item, Extraction())
    return {o.evidence[0].field_path:o.fact.value for o in data.observations}


class ShopifyEvidenceTests(unittest.TestCase):
    def test_price_and_configuration_follow_selected_native_variant(self):
        facts = product_facts(listing())
        self.assertEqual(facts['price'], {'amount':'42.25', 'currency':'USD'})
        self.assertEqual(facts['catalog.configuration_id'], 'soundcore:h30i:black')
        self.assertEqual(facts['route.origin_country'], 'LB')
        self.assertNotIn('catalog.mpn', facts)
        self.assertNotIn('catalog.delivery_city', facts)

    def test_changed_product_or_seller_and_ambiguous_selection_are_refused(self):
        item = listing()
        for mutate in (lambda p:p.update(productGroupID='another'),
                       lambda p:p['hasVariant'].append(p['hasVariant'][1]),
                       lambda p:p['hasVariant'][1]['offers'].update(offeredBy={'name':'Another Merchant'})):
            self.assertFalse(product_facts(item, change=mutate))
        self.assertFalse(product_facts(listing(variant='333')))

    def test_collection_origin_requires_the_exact_link_product_and_variant_together(self):
        item = listing('wincell')
        template = ('<div>{statement}</div><article class="card"><a href="{url}">Item</a><form>'
                    '<input name="id" value="{variant}"><input name="product-id" value="{pid}"></form></article>')
        data = dict(statement=COLLECTION_DISPATCH, url='/products/test-headphones', variant='10', pid='123')
        def observations(values):
            return extract_dispatch_membership(capture(item, COLLECTION, template.format(**values)), item).observations
        self.assertEqual(observations(data)[0].fact.value, 'LB')
        for field, bad in [('url','/products/other'),('variant','11'),('pid','124'),('statement','Delivery across Lebanon')]:
            self.assertFalse(observations({**data, field:bad}))
        self.assertFalse(observations({**data, 'statement':'<script>' + COLLECTION_DISPATCH + '</script>'}))
