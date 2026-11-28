"""Search ranking, local catalog search and the "slow down" memory of the scout. Offline: no network.

- Rare words decide a match (word weights), so a product no shop lists does not return unrelated items.
- A shop with a local catalog is searched without any request; a shop with no index is reported, not
  counted as searched.
- A shop that answers HTTP 429 twice is left alone for a while.
"""
from decimal import Decimal
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import httpx2

from app.scout import web as web_module
from app.scout.catalog import PreparedIndex, ShopSearch, WordWeights, score
from app.scout.shops import Shop
from app.scout.web import FetchError, SafeWeb
from tests.test_scout import ALPHA, NOW, PUBLIC, web

ROWS = [dict(title=t, url=f'https://alpha.example/products/{n}', price_min=p, currency='USD', available=True,
             variant_titles=[], skus=s)
        for n, (t, p, s) in enumerate([
            ('Apple AirPods Pro 2 (USB-C)', '195.00', ['MTJV3ZM/A']),
            ('Segway GoKart Pro 2', '900.00', []),
            ('Samsung Odyssey G5 27 inch Gaming Monitor', '250.00', ['LS27CG552EMXUE']),
            ('Dell Latitude 5440 Laptop', '700.00', []),
            ('Dell Inspiron 15 Laptop', '650.00', []),
            ('Dell 65W USB-C Charger for Latitude', '30.00', []),
            ('Modelling Dough Accessory Kit Pro', '12.00', []),
            ('Anker USB-C Cable 2 m', '9.00', []),
            ('Samsung Galaxy Buds 2 Pro', '120.00', []),
            ('HP Pavilion Pro 15 Laptop', '800.00', [])])]


class RankingTests(unittest.TestCase):
    def setUp(self):
        self.index = PreparedIndex(ROWS)
        self.weights = WordWeights([self.index])

    def test_words_containing_a_partial_code(self):
        self.assertEqual(self.index.containing('ls27cg55'), ['ls27cg552emxue'])
        self.assertEqual(self.index.containing('zzzz'), [])
        whole = self.index.containing('airpods')
        self.assertEqual(whole, ['airpods'])   # first hit, exact word, no neighbour picked up

    def test_a_partial_model_code_finds_the_product_through_its_sku(self):
        top = self.index.best('LS27CG55', 3, self.weights)
        self.assertEqual(top[0][1]['title'], 'Samsung Odyssey G5 27 inch Gaming Monitor')

    def test_rare_words_decide_the_match(self):
        plain = {r['title']: s for s, r in self.index.best('AirPods Pro 2', 5)}
        weighted = {r['title']: s for s, r in self.index.best('AirPods Pro 2', 5, self.weights)}
        self.assertEqual(weighted['Apple AirPods Pro 2 (USB-C)'], 1.0)
        # "Pro" and "2" are everywhere in a catalog; on their own they count for less than before.
        self.assertGreater(plain['Segway GoKart Pro 2'], 0.6)
        self.assertLess(weighted['Segway GoKart Pro 2'], plain['Segway GoKart Pro 2'] - 0.1)

    def test_a_product_no_shop_lists_has_no_strong_match(self):
        strong = [r['title'] for s, r in self.index.best('Dell P2422H', 5, self.weights) if s >= 0.5]
        self.assertEqual(strong, [])

    def test_scores_stay_between_zero_and_one(self):
        empty = WordWeights([])
        for weight in (None, self.weights, empty):
            for query in ('AirPods Pro 2', 'zzzz qqqq', 'for', ''):
                for row in ROWS:
                    self.assertTrue(0.0 <= score(query, row['title'], weight=weight) <= 1.0)


class LocalSearchTests(unittest.TestCase):
    def test_a_local_catalog_is_searched_without_asking_the_shop(self):
        with TemporaryDirectory() as folder:
            (Path(folder) / 'alpha.json').write_text(json.dumps(dict(shop_id='alpha', rows=ROWS)), encoding='utf-8')
            w = web()
            search = ShopSearch(w, [ALPHA], local_dir=Path(folder))
            self.assertEqual(search.warm(), 1)
            report = search.search('LS27CG55')
            self.assertEqual([(h.title, h.price, h.via) for h in report.hits],
                             [('Samsung Odyssey G5 27 inch Gaming Monitor', Decimal('250.00'), 'catalog')])
            self.assertEqual((w.requests, report.shops_answered, report.failures), (0, 1, {}))

    def test_a_shop_without_an_index_is_reported_not_counted_as_searched(self):
        with TemporaryDirectory() as folder:
            shop = Shop('nofile', 'No File', 'https://nofile.example', platform='html', provenance='test')
            report = ShopSearch(web(), [shop], cache_dir=Path(folder)).search('sony wh-ch520')
            self.assertEqual((report.shops_asked, report.shops_answered, report.failures), (1, 0, {'nofile': 'NO_INDEX'}))


class SlowDownTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(web_module._SLOWED.clear)
        self.sent, self.now, self.status = [], dict(t=1000.0), dict(code=429)

        def handler(request):
            self.sent.append(request.url.path)
            return httpx2.Response(self.status['code'], text='{}', headers={'content-type': 'application/json'})
        self.web = SafeWeb(transport=httpx2.MockTransport(handler), resolver=lambda h, p: [PUBLIC],
                           sleep=lambda s: None, min_interval_s=0, obey_robots=False, wall_clock=lambda: NOW,
                           clock=lambda: self.now['t'])

    def ask(self, path='/products/a.js'):
        try:
            self.web.get('https://slow.example' + path, accept='json')
            return 'OK'
        except FetchError as exc:
            return exc.code

    def test_two_refusals_rest_the_shop_and_nothing_more_is_sent(self):
        self.assertEqual(self.ask(), 'RATE_LIMITED')
        self.assertEqual(self.ask(), 'RATE_LIMITED')    # same moment: one episode, a retry is still allowed
        self.now['t'] += 30
        self.assertEqual(self.ask(), 'RATE_LIMITED')    # a second episode: the shop now rests
        sent = len(self.sent)
        self.assertEqual(self.ask(), 'RESTING')
        self.assertEqual(self.ask('/search/suggest.json'), 'RESTING')   # the whole host, not one path
        self.assertEqual(len(self.sent), sent)
        self.now['t'] += web_module.SLOWDOWN_REST_S + 1
        self.status['code'] = 200
        self.assertEqual(self.ask(), 'OK')

    def test_one_refusal_then_success_does_not_rest_the_shop(self):
        self.assertEqual(self.ask(), 'RATE_LIMITED')
        self.status['code'] = 200
        self.now['t'] += 30
        self.assertEqual(self.ask(), 'OK')
        self.assertEqual(self.ask(), 'OK')


if __name__ == '__main__':
    unittest.main()
