"""Synthetic multi-model fixtures derived from the dated Class capture, never live proof."""
import json
from dataclasses import replace
from app.adapters.class_cellulars import ADAPTER, CONFIG, ClassCatalogAdapter
from app.source_capabilities import CapabilityRef
from app.sources import SourceGate
from app.contracts import ToolError
from . import FakeFetch, ReaderTestCase, fixture, query, routes, CLASS_API, CLASS_PAGE

SEARCH = 'https://classcellulars.com/wp-json/wc/store/v1/products?search=iPhone%2016&per_page=10&page=1'
API = 'https://classcellulars.com/wp-json/wc/store/v1/products?slug=iphone-16'
PAGE = 'https://classcellulars.com/product/iphone-16/'

class ScopedFetch(FakeFetch):
    def __call__(self, source_id, url, *, expect, capability=None):
        SourceGate().check_scope(source_id, url, expect=expect, capability=capability)
        return super().__call__(source_id, url, expect=expect)

def records():
    return [dict(id=500, name='iPhone 16', slug='iphone-16', parent=0, type='variable',
        categories=[{'slug':'mobiles'}], permalink=PAGE)]

def table(entries=None):
    # Deliberately synthetic IDs, model and bytes. Preserve field shapes and unknowns.
    api = fixture('class_cellulars/api.json').decode().replace('18392','500').replace('iPhone 17','iPhone 16').replace('iphone-17','iphone-16')
    page = fixture('class_cellulars/page.html').decode().replace('18392','500').replace('iPhone 17','iPhone 16').replace('iphone-17','iphone-16')
    return {SEARCH: json.dumps(records() if entries is None else entries).encode(), API:api.encode(), PAGE:page.encode()}

class CatalogTests(ReaderTestCase):
    def test_discovery_and_refresh_survive_new_adapter_instance(self):
        fetch=ScopedFetch(table())
        result=ADAPTER.search_candidates(fetch,query(model='iPhone 16'))
        self.assertContract(result)
        self.assertEqual(len(result.offers),4)
        self.assertEqual({o.variant.model.value for o in result.offers},{'iPhone 16'})
        self.assertEqual({o.condition.state for o in result.offers},{'unknown'})
        self.assertEqual({str(o.price.value.amount) for o in result.offers},{'1030.00'})
        refreshed=ClassCatalogAdapter(CONFIG).fetch_offer(ScopedFetch(table()), result.offers[0].offer_id,query(model='iPhone 16'))
        self.assertContract(refreshed)
        self.assertEqual(len(refreshed.offers),1)
        self.assertEqual(len(refreshed.tool_result.data['fetches']),3)
        self.assertEqual(len(result.tool_result.data['fetches']),3)

    def test_accessories_unsafe_links_and_wrong_models_never_fetched(self):
        for change in ({'categories':[{'slug':'cases'}]}, {'name':'iPhone 17 Pro'},
                       {'permalink':'https://evil.example/product/iphone-16/'}, {'slug':'checkout'}):
            entries=records();entries[0].update(change)
            fetch=ScopedFetch(table(entries))
            result=ADAPTER.search_candidates(fetch,query(model='iPhone 16'))
            self.assertFalse(result.offers)
            self.assertEqual(len(fetch.calls),1)

    def test_duplicate_product_only_read_once(self):
        fetch=ScopedFetch(table(records()*2))
        self.assertEqual(len(ADAPTER.search_candidates(fetch,query(model='iPhone 16')).offers),4)
        self.assertEqual(len(fetch.calls),3)

    def test_discovery_bound_is_explicit(self):
        fetch=ScopedFetch(table(records()*10))
        result=ADAPTER.search_candidates(fetch,query(model='iPhone 16'))
        self.assertEqual(result.tool_result.status,'partial')
        self.assertEqual(len(fetch.calls),3)

    def test_budget_or_access_failure_preserved(self):
        for code in ('BUDGET_EXHAUSTED','INVALID_SCHEMA'):
            fetch=ScopedFetch({SEARCH:ToolError(code=code,message='bounded failure',retryable=False)})
            result=ADAPTER.search_candidates(fetch,query(model='iPhone 16'))
            self.assertEqual(result.tool_result.status,'error')
            self.assertEqual(result.tool_result.errors[0].code,code)
            self.assertEqual(len(fetch.calls),1)

    def test_malformed_discovery_and_unknown_model(self):
        fetch=ScopedFetch({SEARCH:b'{}'})
        self.assertEqual(ADAPTER.search_candidates(fetch,query(model='iPhone 16')).tool_result.status,'error')
        fetch=ScopedFetch({})
        self.assertFalse(ADAPTER.search_candidates(fetch,query(model='unknown laptop')).offers)
        self.assertFalse(fetch.calls)

    def test_scope_rejects_action_and_unbounded_search(self):
        gate=SourceGate()
        for url in (SEARCH.replace('per_page=10','per_page=100'),SEARCH+'&add-to-cart=5'):
            with self.assertRaises(ValueError):
                gate.check_scope('class_cellulars',url,expect='json',capability=CapabilityRef(capability_id='class-search',version='1'))
