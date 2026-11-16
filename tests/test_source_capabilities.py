"""Exercise the scoped fetch path with synthetic sources and the real gate controls."""
from copy import deepcopy
import gzip
import json

from pydantic import ValidationError

from app.contracts import SourcePolicy
from app.source_capabilities import CapabilityRef
from app.source_registry import get_active_source
from test_source_gate import A1, MAX, PUBLIC, ROOT, GateCase, html_ok, json_ok, redirect, respond


HOST = 'https://alpha.example'
SEARCH = HOST + '/api/products/search?q=ThinkPad%20T14&type=product'
SEARCH_PLUS = SEARCH.replace('%20', '+')
VARIANT = HOST + '/api/products/x1-carbon?variant=834'
PAGE_1 = HOST + '/catalog/laptops?page=1&limit=10'
PAGE_2 = HOST + '/catalog/laptops?page=2&limit=10'
FEED = HOST + '/feeds/electronics.xml'
SITEMAP = HOST + '/sitemaps/products.xml?shard=2'


class CapabilityGateTests(GateCase):
    def setUp(self):
        super().setUp()
        self.registry = json.loads((ROOT / 'tests/fixtures/source-capabilities.json').read_text('utf-8'))
        self.source = self.registry['sources'][0]
        self.write_registry(self.registry)

    def read_registry(self, *args, **kwargs):
        source = super().read_registry(*args, **kwargs)
        # Independently pin the only dynamic URLs these offline tests may put on the wire.
        if source['id'] == 'alpha' and source.get('endpoint_capabilities'):
            self.latest.reads['alpha'].extend((SEARCH, SEARCH_PLUS, VARIANT, PAGE_1, PAGE_2, FEED, SITEMAP))
        return source

    def scoped(self, rule, url, *, version='1', expect='json', source='alpha'):
        return self.gate.fetch(self.run, source, url, expect=expect,
                               capability=CapabilityRef(capability_id=rule, version=version))

    def test_search_variant_and_legacy_get_share_the_gate(self):
        for url in (SEARCH, SEARCH_PLUS, VARIANT, A1):
            self.transport.route(url, json_ok())

        for url in (SEARCH, SEARCH_PLUS):
            self.assertOk(self.scoped('product-search', url))
        self.assertOk(self.scoped('variant-details', VARIANT))
        self.assertOk(self.fetch('alpha', A1))
        self.assertEqual(self.transport.urls, [SEARCH, SEARCH_PLUS, VARIANT, A1])
        self.assertEqual(self.resolver.calls, [('alpha.example', 443)] * 4)
        self.assertEqual(self.run.commits, 4)

    def test_a_source_can_configure_only_capabilities(self):
        del self.source['allowed_endpoints']
        self.write_registry(self.registry)
        self.assertTrue(SourcePolicy.model_validate(get_active_source('alpha', self.path))
                        .endpoint_capabilities)
        self.transport.route(VARIANT, json_ok())
        self.assertOk(self.scoped('variant-details', VARIANT))
        self.assertRefused(self.fetch('alpha', A1), 'BLOCKED', 'blocked:url')
        self.assertEqual(self.transport.urls, [VARIANT])

    def test_catalog_pages_feed_and_sitemap_are_bounded_gets(self):
        feed = b'<rss><channel><title>Synthetic</title></channel></rss>'
        sitemap = b'<urlset><url><loc>https://alpha.example/api/products/x1-carbon</loc></url></urlset>'
        self.transport.route(PAGE_1, html_ok())
        self.transport.route(PAGE_2, html_ok())
        self.transport.route(FEED, respond(200, gzip.compress(feed), 'application/rss+xml',
                                          headers=[('Content-Encoding', 'gzip')]))
        self.transport.route(SITEMAP, respond(200, sitemap, 'application/xml'))

        for url in (PAGE_1, PAGE_2):
            self.assertOk(self.scoped('catalog-page', url, expect='html'))
        result = self.scoped('public-feed', FEED, expect='xml')
        self.assertOk(result)
        self.assertEqual((result.body, result.content_type), (feed, 'application/rss+xml'))
        result = self.scoped('product-sitemap', SITEMAP, expect='xml')
        self.assertOk(result)
        self.assertEqual((result.body, result.content_type), (sitemap, 'application/xml'))
        self.assertEqual(self.run.commits, 4)

    def test_bad_urls_are_refused_before_dns_or_budget(self):
        bad_searches = (
            SEARCH + '&q=second', SEARCH + '&Q=second', SEARCH + '&action=checkout',
            SEARCH + '&_method=POST', SEARCH + '&.method=DELETE', SEARCH + '&extra=1',
            SEARCH + ';add-to-cart=1', SEARCH + '#ignored',
            SEARCH.replace('T14', '%26type%3Dproduct'), SEARCH.replace('T14', '%2520junk'),
            SEARCH.replace('ThinkPad%20T14', 'x' * 49), SEARCH.replace('ThinkPad%20T14', 'Checkout'),
            SEARCH.replace('ThinkPad%20T14', 'foo%20Cart'), SEARCH.replace('ThinkPad%20T14', ''),
            SEARCH.replace('ThinkPad%20T14', 'foo%09bar'), SEARCH.replace('ThinkPad%20T14', 'foo bar'),
            SEARCH.replace('&type=product', ''), SEARCH.replace('type=product', 'type=page'),
            SEARCH.replace('/products/', '/products/../products/'),
            SEARCH.replace('/products/', '/%70roducts/'),
            SEARCH.replace('alpha.example', 'evil.example'),
            SEARCH.replace('alpha.example', 'alpha.example:443'),
            SEARCH.replace('alpha.example', 'user@alpha.example'),
            SEARCH.replace('alpha.example', '127.0.0.1'),
            SEARCH.replace('https://', 'http://'),
        )
        for url in bad_searches:
            with self.subTest(url=url):
                self.assertRefused(self.scoped('product-search', url), 'BLOCKED', 'blocked:url')
        for value in ('0', '-2', '001', '1.0', '1e3', '1000000000000', '834%20'):
            with self.subTest(variant=value):
                self.assertRefused(self.scoped('variant-details', VARIANT.replace('834', value)),
                                   'BLOCKED', 'blocked:url')
        for url in (VARIANT.replace('x1-carbon', 'cart'), VARIANT.replace('x1-carbon', 'add_to_cart'),
                    VARIANT.replace('x1-carbon', 'x1%2fcarbon'),
                    VARIANT.replace('x1-carbon', 'other/x1-carbon')):
            self.assertRefused(self.scoped('variant-details', url), 'BLOCKED', 'blocked:url')
        for url in (PAGE_1.replace('page=1', 'page=6'), PAGE_1.replace('page=1', 'page=0'),
                    PAGE_1.replace('limit=10', 'limit=21'), PAGE_1.replace('laptops', 'cameras')):
            self.assertRefused(self.scoped('catalog-page', url, expect='html'), 'BLOCKED', 'blocked:url')
        self.assertRefused(self.scoped('product-sitemap', SITEMAP.replace('shard=2', 'shard=5'), expect='xml'),
                           'BLOCKED', 'blocked:url')
        self.assertNothingSent()

    def test_malformed_capability_configuration_fails_closed(self):
        cases = (
            lambda r: r.update(host='evil.example'),
            lambda r: r.update(host='*.example'),
            lambda r: r.update(path=['api', '*']),
            lambda r: r.update(path=['api', '{arbitrary_url}']),
            lambda r: r.update(path=['api', 'checkout']),
            lambda r: r.update(path=[{'kind': 'token', 'max_length': 12}]),
            lambda r: r.update(operation='post'),
            lambda r: r.update(url_template='https://alpha.example/{anything}'),
            lambda r: r['query'][0].update(name='_method'),
            lambda r: r['query'][0].update(name='wc-ajax'),
            lambda r: r['query'][0].update(parameter={'kind': 'search_text'}),
            lambda r: r['query'][0].update(parameter={'kind': 'integer', 'minimum': 6, 'maximum': 5}),
            lambda r: r['query'][0].update(parameter={'kind': 'token', 'max_length': True}),
            lambda r: r['query'][1].update(parameter={'kind': 'choice', 'values': ['cart']}),
            lambda r: r['query'].append(deepcopy(r['query'][0])),
        )
        for index, mutate in enumerate(cases):
            with self.subTest(case=index):
                candidate = deepcopy(self.source)
                mutate(candidate['endpoint_capabilities'][0])
                self.use_registry([candidate])
                with self.assertRaises(PermissionError):
                    get_active_source('alpha', self.path)
                with self.assertRaises(ValidationError):
                    SourcePolicy.model_validate(candidate)
                self.assertRefused(self.fetch('alpha', A1), 'SOURCE_DISABLED')
        candidate = deepcopy(self.source)
        candidate['endpoint_capabilities'].append(candidate['endpoint_capabilities'][0])
        self.use_registry([candidate])
        self.assertRefused(self.fetch('alpha', A1), 'SOURCE_DISABLED')
        self.assertNothingSent()

    def test_discovery_does_not_supply_permission_and_refs_do_not_expand_scope(self):
        for rule, url, version, expect in (
            ('missing', SEARCH, '1', 'json'),
            ('product-search', SEARCH, '0', 'json'),
            ('product-search', SEARCH, '1', 'html'),
            ('product-search', VARIANT, '1', 'json'),
            ('product-search', A1, '1', 'json'),
        ):
            self.assertRefused(self.scoped(rule, url, version=version, expect=expect), 'BLOCKED', 'blocked:url')
        self.assertRefused(self.fetch('alpha', SEARCH, expect='json'), 'BLOCKED', 'blocked:url')
        self.assertRefused(self.scoped('product-search', SEARCH, source='directory-lead'), 'SOURCE_DISABLED')
        self.registry['live_release']['admitted_source_ids'] = []
        self.write_registry(self.registry)
        self.assertRefused(self.scoped('product-search', SEARCH), 'SOURCE_DISABLED')
        self.assertNothingSent()

    def test_redirects_cannot_switch_rules_or_leave_the_scope(self):
        for location in (FEED, 'https://beta.example/catalog/laptops?page=2', 'http://169.254.169.254/',
                         '/catalog/laptops?page=6', '/catalog/laptops?page=2&checkout=1'):
            with self.subTest(location=location):
                self.reset()
                self.transport.route(PAGE_1, redirect(location))
                self.assertRefused(self.scoped('catalog-page', PAGE_1, expect='html'),
                                   'BLOCKED', 'blocked:redirect')
                self.assertEqual(self.transport.urls, [PAGE_1])
                self.assertEqual(self.run.commits, 1)

    def test_redirect_hops_recheck_dns_and_share_workload_caps(self):
        self.reset({'alpha.example': [[PUBLIC], ['10.0.0.5']]})
        self.transport.route(PAGE_1, redirect('?page=2&limit=10'))
        self.assertRefused(self.scoped('catalog-page', PAGE_1, expect='html'), 'BLOCKED', 'blocked:dns')
        self.assertEqual(self.transport.urls, [PAGE_1])
        self.assertEqual(self.resolver.calls, [('alpha.example', 443)] * 2)
        self.assertEqual(self.run.commits, 1)

        self.source['limits']['max_requests_per_run'] = 2
        self.write_registry(self.registry)
        self.reset()
        self.transport.route(PAGE_1, redirect('?page=2&limit=10'))
        self.transport.route(PAGE_2, html_ok())
        followed = self.scoped('catalog-page', PAGE_1, expect='html')
        self.assertOk(followed)
        self.assertEqual(followed.redirect_chain, (PAGE_1, PAGE_2))
        self.assertRefused(self.scoped('variant-details', VARIANT), 'BUDGET_EXHAUSTED')
        self.assertEqual(self.transport.urls, [PAGE_1, PAGE_2])
        self.assertEqual(self.run.commits, 2)

    def test_rules_can_be_revoked_between_attempts(self):
        self.transport.route(SEARCH, respond(502))
        sleep = self.sleep

        def revoke_during_retry(seconds):
            sleep(seconds)
            self.source['endpoint_capabilities'][0]['version'] = '2'
            self.write_registry(self.registry)

        self.gate = self.make_gate(sleep=revoke_during_retry)
        refused = self.scoped('product-search', SEARCH)
        self.assertEqual(self.codes(refused), [('NETWORK_ERROR', True), ('BLOCKED', False)])
        self.assertEqual(self.transport.urls, [SEARCH])
        self.assertEqual(self.run.commits, 1)

    def test_xml_uses_the_same_content_and_size_checks(self):
        self.transport.route(SITEMAP, html_ok())
        self.assertRefused(self.scoped('product-sitemap', SITEMAP, expect='xml'), 'INVALID_SCHEMA')
        self.transport.route(SITEMAP, respond(200, b'<urlset/>', 'application/xml',
                                             headers=[('Content-Length', str(MAX + 1))]))
        self.assertRefused(self.scoped('product-sitemap', SITEMAP, expect='xml'), 'BLOCKED', 'blocked:size')
