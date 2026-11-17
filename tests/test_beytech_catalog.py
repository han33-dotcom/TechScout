"""Beytech real minimized captures and synthetic cross-product catalog regressions."""
import copy
import html
import json
from pathlib import Path
import unittest

from app.adapters.beytech import CATALOG
from app.contracts import ToolError
from adapters import FakeFetch,BEY_API,BEY_PAGE,BEY_WARRANTY,BEY_EXCHANGE,fixture
from test_woo_catalog import attribute,product,facts,ScopedFetch

ORIGIN='https://beytech.com.lb'
SEARCH=ORIGIN+'/wp-json/wc/store/v1/products?search=iPhone%2017&per_page=10&page=1'


def table():
    records=json.loads(fixture('beytech/api.json'))
    parent=next(r for r in records if r['parent']==0)
    parent['categories']=[{'slug':'mobiles'}]
    return {SEARCH:json.dumps(records).encode(),BEY_API:json.dumps(records).encode(),BEY_PAGE:fixture('beytech/page.html')}


class BeytechCatalogTests(unittest.TestCase):
    def test_two_live_captured_models_produce_ten_distinct_variants(self):
        rows=json.loads(fixture('beytech/catalog-2026-11-17.json'))
        parents=[r for row in rows for r in row['api'] if r['parent']==0]
        t={SEARCH:json.dumps(parents).encode()}
        for row in rows:
            t[row['api_url']]=json.dumps(row['api']).encode()
            t[row['page_url']]=''.join('<form '+ ' '.join(k+'="'+html.escape(v or '',quote=True)+'"' for k,v in form.items())+'></form>' for form in row['forms']).encode()
        result=CATALOG.search(ScopedFetch(t),category='phone',search='iPhone 17')
        self.assertEqual(result.tool_result.status,'ok')
        self.assertEqual(len(result.candidates),10)
        self.assertEqual(len({(c.product_id,c.variant_id) for c in result.candidates}),10)
        self.assertEqual(len({c.product_id for c in result.candidates}),2)
        self.assertTrue(all(c.priced for c in result.candidates))

    def test_real_parent_and_variation_records_share_equivalent_decimal_price(self):
        r=CATALOG.search(ScopedFetch(table()),category='phone',search='iPhone 17')
        self.assertEqual(r.tool_result.status,'ok')
        self.assertEqual(len(r.candidates),4)
        for c in r.candidates:
            f=facts(c)
            self.assertTrue(c.priced)
            self.assertEqual(f['price'].value,{'amount':'1079','currency':'USD'})
            self.assertEqual(f['variant.specs.storage_gb'].value,256)
            self.assertNotIn('variant.specs.sim',f)
            self.assertNotIn('condition',f)
            self.assertTrue(c.selected_attributes)

    def test_two_different_products_and_identical_parent_repeats(self):
        t=table();records=json.loads(t[BEY_API]);parent=next(r for r in records if r['parent']==0)
        other=copy.deepcopy(parent);other.update(id=200000,slug='another-phone',permalink=ORIGIN+'/product/another-phone/',type='simple',variations=[],attributes=[attribute('Model',['Other Phone']),attribute('Storage',['512 GB'])])
        t[SEARCH]=json.dumps([parent,parent,other]).encode()
        t[BEY_API]=json.dumps(records+[parent]).encode()
        t[ORIGIN+'/wp-json/wc/store/v1/products?slug=another-phone']=json.dumps([other]).encode()
        t[other['permalink']]=b'<html>Other phone</html>'
        r=CATALOG.search(ScopedFetch(t),category='phone',search='iPhone 17')
        self.assertEqual({c.product_id for c in r.candidates},{'139968','200000'})
        self.assertEqual(len(r.candidates),5)
        self.assertEqual(facts(r.candidates[-1])['variant.specs.storage_gb'].value,512)

    def test_laptop_and_monitor_simple_products_keep_source_identity(self):
        for category,slug,attrs in [('laptop','laptops',[attribute('CPU',['Intel Core i5']),attribute('RAM',['16 GB'])]),('monitor','monitors',[attribute('Resolution',['1920 x 1080']),attribute('Refresh Rate',['144 Hz'])])]:
            p=product(category=slug,permalink=ORIGIN+'/product/example-101/',attributes=attrs)
            t={SEARCH:json.dumps([p]).encode(),ORIGIN+'/wp-json/wc/store/v1/products?slug=example-101':json.dumps([p]).encode(),p['permalink']:b'<html>Example</html>'}
            r=CATALOG.search(ScopedFetch(t),category=category,search='iPhone 17')
            self.assertEqual(len(r.candidates),1)
            self.assertTrue(r.candidates[0].priced)
            self.assertEqual(r.candidates[0].source_id,'beytech')

    def test_explicit_variant_condition_only(self):
        t=table();records=json.loads(t[BEY_API]);parent=next(r for r in records if r['parent']==0)
        parent['attributes'].append(attribute('Condition',['new']))
        t[BEY_API]=json.dumps(records).encode()
        r=CATALOG.search(ScopedFetch(t),category='phone',search='iPhone 17')
        self.assertEqual(facts(r.candidates[0])['condition'].value,'new')
        self.assertTrue(all(e.scope=='variant' for o in r.candidates[0].extraction.observations if o.evidence[0].field_path=='condition' for e in o.evidence))

    def test_policy_receipts_and_missing_tax_condition_are_explicit(self):
        f=FakeFetch({BEY_WARRANTY:fixture('beytech/warranty.html'),BEY_EXCHANGE:fixture('beytech/exchange.html')})
        r=CATALOG.fetch_policies(f)
        observations={o.evidence[0].field_path:o for o in r.extraction.observations}
        self.assertIn('repairs only',observations['policy.warranty'].fact.value)
        self.assertIn('original sales invoice',observations['policy.warranty'].fact.value)
        self.assertIn('tax',r.missing_topics)
        self.assertIn('condition',r.missing_topics)
        self.assertFalse(r.tool_result.data['resolved_offer_costs'])
        self.assertEqual(f.urls(),[BEY_WARRANTY,BEY_EXCHANGE])
        self.assertTrue(all(e.scope=='source' for o in r.extraction.observations for e in o.evidence))

    def test_published_policy_statements_are_text_not_invented_fee_values(self):
        body=b'<p>Delivery all over Lebanon. All prices include VAT. All devices are brand new.</p>'
        r=CATALOG.fetch_policies(FakeFetch({BEY_WARRANTY:body,BEY_EXCHANGE:body}))
        observations={o.evidence[0].field_path:o.fact.value for o in r.extraction.observations}
        self.assertEqual(observations['policy.tax'],'All prices include VAT')
        self.assertEqual(observations['policy.condition'],'All devices are brand new')
        self.assertEqual(r.missing_topics,('warranty',))
        self.assertNotIn('costs.order.tax',observations)

    def test_policy_denial_retains_other_page_and_reports_partial(self):
        r=CATALOG.fetch_policies(FakeFetch({BEY_WARRANTY:ToolError(code='BUDGET_EXHAUSTED',message='test',retryable=False),BEY_EXCHANGE:fixture('beytech/exchange.html')}))
        self.assertEqual(r.tool_result.status,'partial')
        self.assertIn('warranty',r.missing_topics)

if __name__=='__main__':unittest.main()
