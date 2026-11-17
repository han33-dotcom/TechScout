"""Synthetic cross-category discovery and adversarial evidence checks; no network."""
import copy
import html
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from app.adapters.mojitech import CATALOG
from app.source_registry import get_active_source
from app.source_capabilities import EndpointCapability
from adapters import FakeFetch, MOJI_API, MOJI_PAGE, fixture

ORIGIN = 'https://mojitech.net'
SEARCH = ORIGIN+'/wp-json/wc/store/v1/products?search=Example&per_page=10&page=1'


def attribute(name, values, varies=False):
    return {'name':name,'taxonomy':None,'has_variations':varies,
            'terms':[{'slug':v.lower().replace(' ','-'),'name':v} for v in values]}


def product(pid=101, category='laptops', **changes):
    return {'id':pid,'parent':0,'type':'simple','name':'Example laptop',
            'slug':f'example-{pid}','permalink':f'{ORIGIN}/shop/brands/example/example-{pid}/',
            'categories':[{'slug':category}],
            'attributes':[attribute('Brand',['Example']),attribute('Model',['X14']),
                          attribute('CPU',['Core Ultra 5 125H']),attribute('RAM',['16 GB']),
                          attribute('Storage',['512 GB'])],
            'variations':[], 'prices':{'price':'79900','currency_code':'USD','currency_minor_unit':2},
            'is_purchasable':True,'is_in_stock':True, **changes}


class ScopedFetch(FakeFetch):
    def __call__(self, source_id, url, *, expect, capability=None):
        source=get_active_source(source_id)
        rules=[EndpointCapability.model_validate(c) for c in source['endpoint_capabilities']]
        assert any(c.capability_id==capability.capability_id and c.version==capability.version
                   and c.expect==expect and c.matches(url) for c in rules), url
        return super().__call__(source_id,url,expect=expect)


def routes(products, exact=None, pages=None):
    table={SEARCH:json.dumps(products).encode()}
    for p in products:
        table[ORIGIN+'/wp-json/wc/store/v1/products?slug='+p['slug']]=json.dumps((exact or {}).get(p['id'],[p])).encode()
        table[p['permalink']]=(pages or {}).get(p['id'],b'<html><body>Example product</body></html>')
    return table


def facts(candidate):
    return {o.evidence[0].field_path:o.fact for o in candidate.extraction.observations}


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.network=patch('socket.getaddrinfo',side_effect=AssertionError('offline tests'))
        self.network.start(); self.addCleanup(self.network.stop)

    def search(self, products, category='laptop', **kwargs):
        fetch=ScopedFetch(routes(products,**kwargs))
        result=CATALOG.search(fetch,category=category,search='Example')
        return fetch,result

    def test_exact_laptop_configuration_and_decimal_price(self):
        fetch,result=self.search([product()])
        self.assertEqual(result.tool_result.status,'ok')
        c=result.candidates[0]; f=facts(c)
        self.assertEqual((c.product_id,c.variant_id,c.category),('101','101','laptop'))
        self.assertEqual(f['variant.specs.ram_gb'].value,16)
        self.assertEqual(f['variant.specs.cpu'].value,'Core Ultra 5 125H')
        self.assertEqual(f['price'].value,{'amount':'799.00','currency':'USD'})
        self.assertTrue(c.priced)
        self.assertEqual(len(fetch.calls),3)
        hashes={s.sha256 for s in result.snapshots}
        for obs in c.extraction.observations:
            for e in obs.evidence:
                self.assertEqual((e.product_id,e.variant_id,e.scope),('101','101','variant'))
                self.assertIn(e.response_sha256,hashes)

    def test_selects_priced_alternative_instead_of_zero_or_nonpurchasable(self):
        a=product(prices={'price':'0','currency_code':'USD','currency_minor_unit':2})
        b=product(102)
        _,r=self.search([a,b])
        self.assertEqual([c.priced for c in r.candidates],[False,True])
        for raw in ('','call for price','-1','1.23'):
            a['prices']['price']=raw
            _,r=self.search([a]); self.assertFalse(r.candidates[0].priced)
        a=product(is_purchasable=False)
        _,r=self.search([a]); self.assertEqual(facts(r.candidates[0])['price'].state,'unknown')

    def test_monitor_and_phone_specs_do_not_infer_irrelevant_facts(self):
        p=product(category='monitors',attributes=[attribute('Model',['M27']),attribute('Size',['27 inches']),attribute('Refresh Rate',['165 Hz']),attribute('Resolution',['2560 x 1440'])])
        _,r=self.search([p],category='monitor')
        self.assertEqual(facts(r.candidates[0])['variant.specs.refresh_hz'].value,165)
        self.assertNotIn('variant.specs.ram_gb',facts(r.candidates[0]))
        self.assertEqual(facts(r.candidates[0])['variant.specs.width_px'].value,2560)
        p=product(category='smartphones',attributes=[attribute('Storage',['256 GB']),attribute('SIM',['1 Sim'])])
        _,r=self.search([p],category='phone')
        self.assertEqual(facts(r.candidates[0])['variant.specs.storage_gb'].value,256)
        self.assertNotIn('variant.specs.sim',facts(r.candidates[0]))

    def variable(self):
        p=product(type='variable',attributes=[attribute('RAM',['16 GB','32 GB'],True)],
                  variations=[{'id':201,'attributes':[{'name':'RAM','value':'16-gb'}]},
                              {'id':202,'attributes':[{'name':'RAM','value':'32-gb'}]}])
        entries=[{'variation_id':vid,'attributes':{'attribute_ram':f'{ram}-gb'},'display_price':price,
                  'is_purchasable':True,'is_in_stock':True} for vid,ram,price in [(201,16,799),(202,32,999)]]
        def page(rows):
            return ('<form class="variations_form" data-product_id="101" data-product_variations="'+html.escape(json.dumps(rows),quote=True)+'"></form>').encode()
        return p,entries,page

    def test_variable_prices_bind_selected_configuration_not_parent_range(self):
        p,entries,page=self.variable()
        _,r=self.search([p],pages={101:page(entries)})
        self.assertEqual([facts(c)['price'].value['amount'] for c in r.candidates],['799','999'])
        self.assertEqual([facts(c)['variant.specs.ram_gb'].value for c in r.candidates],[16,32])
        self.assertEqual([c.variant_id for c in r.candidates],['201','202'])
        # An unbound form or a wildcard attribute never inherits a parent price.
        entries[0]['attributes']['attribute_ram']=''
        _,r=self.search([p],pages={101:page(entries)})
        self.assertFalse(r.candidates[0].priced)
        self.assertEqual(facts(r.candidates[0])['variant.specs.ram_gb'].state,'unknown')
        self.assertTrue(r.candidates[1].priced)

    def test_duplicate_forms_agree_or_preserve_conflict(self):
        p,entries,page=self.variable()
        _,r=self.search([p],pages={101:page(entries+entries)})
        self.assertEqual(len(r.candidates),2)
        other=copy.deepcopy(entries[0]);other['display_price']=850
        _,r=self.search([p],pages={101:page(entries+[other])})
        self.assertEqual(facts(r.candidates[0])['price'].state,'conflict')
        self.assertFalse(r.candidates[0].priced)

    def test_parent_variation_record_and_form_price_conflict(self):
        p,entries,page=self.variable()
        child=product(201,parent=101,type='variation',attributes=[])
        child['prices']['price']='85000'
        _,r=self.search([p],exact={101:[child,p]},pages={101:page(entries)})
        self.assertEqual(facts(r.candidates[0])['price'].state,'conflict')
        # Parent price cannot replace missing per-variation values.
        _,r=self.search([p],pages={101:page([])})
        self.assertTrue(all(not c.priced for c in r.candidates))

    def test_accessories_unsafe_urls_and_changed_identity_rejected(self):
        _,r=self.search([product(category='laptop-bags')]);self.assertEqual(r.candidates,())
        p=product(permalink='https://evil.example/product/laptop/')
        f,r=self.search([p]);self.assertEqual(len(f.calls),1);self.assertEqual(r.candidates,())
        p=product();changed=copy.deepcopy(p);changed['id']=103
        _,r=self.search([p],exact={101:[changed]});self.assertEqual(r.candidates,())

    def test_duplicate_catalog_identity_is_not_last_write_wins(self):
        p=product();other=copy.deepcopy(p);other['slug']='other'
        _,r=self.search([p,other]);self.assertEqual(r.tool_result.status,'error');self.assertEqual(r.candidates,())

    def test_bounds_and_refresh_do_not_reuse_a_cached_price(self):
        f,r=self.search([product(i) for i in range(101,104)])
        self.assertLessEqual(len(f.calls),5)
        self.assertEqual(r.tool_result.status,'partial')
        p=product();f=ScopedFetch(routes([p]))
        first=CATALOG.search(f,category='laptop',search='Example')
        p['prices']['price']='89900';f.table=routes([p])
        second=CATALOG.search(f,category='laptop',search='Example',only={('101','101')})
        self.assertEqual(facts(second.candidates[0])['price'].value['amount'],'899.00')
        self.assertNotEqual(facts(first.candidates[0])['price'].evidence_ids,facts(second.candidates[0])['price'].evidence_ids)

    def test_legacy_capture_is_still_unpriced_in_v2(self):
        records=json.loads(fixture('mojitech/api.json'))
        parent=next(r for r in records if r['parent']==0)
        parent['categories']=[{'slug':'iphones'}]
        fetch=ScopedFetch({SEARCH:json.dumps([parent]).encode(),MOJI_API:json.dumps(records).encode(),MOJI_PAGE:fixture('mojitech/page.html')})
        r=CATALOG.search(fetch,category='phone',search='Example')
        self.assertEqual(len(r.candidates),12)
        self.assertTrue(all(not c.priced for c in r.candidates))

if __name__=='__main__':
    unittest.main()
