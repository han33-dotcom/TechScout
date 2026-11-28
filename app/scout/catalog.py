"""Search many Lebanese shops at once, and keep a local index for shops that forbid live search.

``ShopSearch.search(query)`` asks every enabled shop in parallel:

* Shopify shops whose robots.txt allows ``/search/suggest.json`` and WooCommerce shops
  (Store API search) are asked live, so prices and stock are current.
* Shopify shops that disallow search (Abed Tahan, JAK Computer) and shops that publish a
  merchant feed (Technodel) are searched in a local index built from their public catalog
  (``/products.json`` or the feed). ``refresh`` rebuilds that index; run it daily.

Search results are leads. The agent must open a product (current-run read) before it can
claim a price or stock state.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import math
from pathlib import Path
import re
import sys
import threading
import time

from app.scout import shops as shop_dir
from app.scout.storefronts import Hit, Shopify, WooCommerce, money, parse_merchant_feed
from app.scout.web import FetchError, SafeWeb

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / 'var' / 'scout-catalog'
INDEX_MAX_AGE = timedelta(days=2)
SEARCH_SECONDS = 9.0  # shops that have not answered by then are reported as SLOW and skipped
LOCAL_SEARCH_SECONDS = 4.0  # the same limit when most shops are searched in a local catalog
TOKEN = re.compile(r'[a-z0-9]+(?:\.[0-9]+)?')
STOP = {'the', 'a', 'an', 'and', 'or', 'for', 'with', 'new', 'of', 'in', 'to', 'by', 'gb', 'inch'}


def tokens(text: str) -> list[str]:
    return TOKEN.findall((text or '').lower())


ACCESSORY = {'case', 'cover', 'protector', 'glass', 'tempered', 'film', 'charger', 'cable', 'adapter', 'strap',
             'band', 'holder', 'stand', 'mount', 'skin', 'sleeve', 'pouch', 'tips', 'lens', 'replacement',
             'battery', 'pen', 'stylus', 'keyboard', 'folio', 'wallet', 'magsafe', 'dock', 'hub', 'compatible'}


def accessory_penalty(query: str, title: str) -> float:
    """Listings for cases, chargers and the like rank below the device unless the buyer asked for one."""
    words = set(tokens(title))
    wanted = set(tokens(query))
    extra = (words & ACCESSORY) - wanted
    if not extra:
        return 1.0
    if 'for' in words or 'compatible' in words or len(extra) >= 2:
        return 0.35
    return 0.6


class WordWeights:
    """How distinctive each word is across all indexed product titles (inverse document frequency).

    "airpods" appears in few titles and decides a match; "pro" and "2" appear everywhere and decide
    little. A word no shop lists is treated as rare, not as infinitely rare, so one unknown part
    number does not hide every listing of the product."""

    def __init__(self, indexes: list['PreparedIndex']):
        self.n = sum(len(i.rows) for i in indexes)
        self.df: dict[str, int] = defaultdict(int)
        for index in indexes:
            for word, rows in index.lookup.items():
                self.df[word] += len(rows)
        self.floor = max(1.0, self.n * 0.002)

    def __call__(self, word: str) -> float:
        return math.log(1 + self.n / (1 + max(self.df.get(word, 0), self.floor))) if self.n else 1.0


def score(query: str, title: str, extra: str = '', weight=None) -> float:
    """Share of the query's distinctive words that appear in the title (extra text counts half),
    scaled down for accessory listings the buyer did not ask for. With ``weight`` (word -> weight),
    rare words count more than common ones."""
    q = [t for t in tokens(query) if t not in STOP] or tokens(query)
    if not q:
        return 0.0
    words, more = set(tokens(title)), set(tokens(extra))
    joined = ''.join(tokens(title))
    got = total = 0.0
    for t in q:
        w = weight(t) if weight else 1.0
        total += w
        if t in words or (len(t) >= 4 and t in joined):
            got += w
        elif t in more:
            got += 0.5 * w
    return got / total * accessory_penalty(query, title) if total else 0.0


class PreparedIndex:
    """A shop's index rows with their words worked out once, and a word -> rows lookup.

    Scoring every title of a 27,000-product index in pure Python for each query took most of a
    search's time (and starved parallel searches of CPU). A search now scores only the rows that
    share a word with the query, using words computed when the index was loaded."""

    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.title_words: list[set[str]] = []
        self.joined: list[str] = []
        self.more_words: list[set[str]] = []
        self.lookup: dict[str, list[int]] = defaultdict(list)
        for i, r in enumerate(rows):
            title = tokens(r.get('title', ''))
            more = tokens(' '.join(r.get('variant_titles') or []) + ' ' + ' '.join(r.get('skus') or [])
                          + ' ' + str(r.get('vendor') or ''))
            words = set(title)
            self.title_words.append(words)
            self.joined.append(''.join(title))
            self.more_words.append(set(more))
            for w in words | set(more):
                self.lookup[w].append(i)
        self.vocabulary = list(self.lookup)
        # All words in one string, so "which words contain this code" is one fast scan, not a Python loop.
        self._blob = '\n'.join(self.vocabulary)
        self._starts, at = [], 0
        for word in self.vocabulary:
            self._starts.append(at)
            at += len(word) + 1

    def containing(self, part: str) -> list[str]:
        """Index words that contain ``part`` (a partial model code such as "ls27cg55")."""
        out, pos = [], self._blob.find(part)
        while pos >= 0:
            i = bisect_right(self._starts, pos) - 1
            out.append(self.vocabulary[i])
            pos = self._blob.find(part, self._starts[i] + len(self.vocabulary[i]) + 1)
        return out

    def best(self, query: str, k: int, weight=None) -> list[tuple[float, dict]]:
        q = [t for t in tokens(query) if t not in STOP] or tokens(query)
        if not q:
            return []
        wanted = set(tokens(query))
        weights = [weight(t) if weight else 1.0 for t in q]
        total = sum(weights) or 1.0
        candidates = set()
        for t in q:
            candidates.update(self.lookup.get(t, ()))
            if len(t) >= 4:  # partial model codes: "ls27cg55" finds "ls27cg552emxue", "mly33" finds "mly33pa"
                for word in self.containing(t):
                    if word != t:
                        candidates.update(self.lookup[word])
        scored = []
        for i in candidates:
            words, joined, more = self.title_words[i], self.joined[i], self.more_words[i]
            got = 0.0
            for t, w in zip(q, weights):
                if t in words or (len(t) >= 4 and t in joined):
                    got += w
                elif t in more or (len(t) >= 4 and any(t in m for m in more)):
                    got += 0.5 * w
            extra = (words & ACCESSORY) - wanted
            penalty = 1.0 if not extra else (0.35 if 'for' in words or 'compatible' in words or len(extra) >= 2
                                             else 0.6)
            scored.append((got / total * penalty, self.rows[i]))
        scored.sort(key=lambda x: -x[0])
        return scored[:k]


_INDEXES: dict[str, tuple[float, PreparedIndex]] = {}   # index file path -> (mtime, prepared index)
_INDEXES_LOCK = threading.Lock()


def load_index(path: Path) -> PreparedIndex:
    """One prepared index per file and process, reloaded only when the file changes."""
    key = str(path)
    mtime = path.stat().st_mtime if path.is_file() else 0.0
    with _INDEXES_LOCK:
        cached = _INDEXES.get(key)
        if cached and cached[0] == mtime:
            return cached[1]
        rows = json.loads(path.read_text(encoding='utf-8')).get('rows', []) if mtime else []
        prepared = PreparedIndex(rows)
        _INDEXES[key] = (mtime, prepared)
        return prepared


@dataclass
class SearchReport:
    hits: list[Hit]
    shops_asked: int
    shops_answered: int
    failures: dict[str, str]
    seconds: float
    local_s: float = 0.0                     # time spent in local index and catalog lookups
    slowest: tuple = ()                      # the slowest live shops: (shop_id, seconds)


class ShopSearch:
    def __init__(self, web: SafeWeb, shops: list | None = None, *, cache_dir: Path = CACHE_DIR,
                 now=lambda: datetime.now(timezone.utc), workers: int = 12, local_dir: Path | None = None,
                 weights: 'WordWeights | None' = None):
        # local_dir: a folder of full shop catalogs (title, price, stock per product). A shop that has a
        # file there is searched locally instead of through its live search endpoint.
        self.local_dir = Path(local_dir) if local_dir else None
        self.weights = weights   # how distinctive each word is; built by warm() or passed in
        self.web = web
        self.shops = [s for s in (shops if shops is not None else shop_dir.load()) if s.enabled]
        self.cache_dir, self.now, self.workers = Path(cache_dir), now, workers
        self._index: dict[str, list[dict]] = {}

    # ---- live + indexed search -------------------------------------------------------------
    def search(self, query: str, *, category: str | None = None, shop_ids: list[str] | None = None,
               per_shop: int = 6, limit: int = 40, deadline: float | None = None) -> SearchReport:
        started = time.monotonic()
        chosen = [s for s in self.shops
                  if (not shop_ids or s.shop_id in shop_ids)
                  and (not category or not s.categories or category in s.categories)]
        failures: dict[str, str] = {}
        results: list[Hit] = []
        answered = 0
        # With local catalogs only a few shops are still asked live: they get a shorter wait.
        limit_s = SEARCH_SECONDS if self.local_dir is None else LOCAL_SEARCH_SECONDS
        wait_s = limit_s if deadline is None else max(1.0, min(limit_s, deadline - time.monotonic()))
        # Shops searched in local data are looked up in this thread, so a slow live shop can never
        # delay or drop them; only shops that need a live request go to the pool.
        local = [s for s in chosen if self.is_local(s)]
        live = [s for s in chosen if not self.is_local(s)]
        took: dict[str, float] = {}

        def timed(shop):
            t0 = time.monotonic()
            try:
                return self._one(shop, query, per_shop, deadline)
            finally:
                took[shop.shop_id] = round(time.monotonic() - t0, 2)
        pool = ThreadPoolExecutor(max_workers=max(1, min(len(live), 2 * self.workers)))
        futures = {pool.submit(timed, s): s for s in live}
        t_local = time.monotonic()
        for shop in local:
            try:
                results.extend(self._one(shop, query, per_shop, deadline))
                answered += 1
            except FetchError as exc:
                failures[shop.shop_id] = exc.code
            except Exception as exc:  # noqa: BLE001 - one shop never sinks the search
                failures[shop.shop_id] = type(exc).__name__
        local_s = round(time.monotonic() - t_local, 2)
        done, late = wait(futures, timeout=max(0.1, wait_s - (time.monotonic() - started)))
        pool.shutdown(wait=False, cancel_futures=True)  # a slow shop is dropped, never waited for
        for fut in late:
            failures[futures[fut].shop_id] = 'SLOW'
        for fut in done:
            shop = futures[fut]
            try:
                hits = fut.result()
                answered += 1
                results.extend(hits)
            except FetchError as exc:
                failures[shop.shop_id] = exc.code
            except Exception as exc:  # noqa: BLE001 - one shop never sinks the search
                failures[shop.shop_id] = type(exc).__name__
        def strength(h):   # an index row matched through its SKU or variant title keeps that match
            return max(self.rank(query, h.title), h.match)
        ranked = sorted((h for h in results if strength(h) > 0),
                        key=lambda h: (-strength(h), h.available is False, h.price if h.price is not None else Decimal('1e9')))
        # Every shop keeps its best hit before the list is cut, so shops whose index has no price survive.
        firsts, rest, seen_shops = [], [], set()
        for h in ranked:
            (rest if h.shop_id in seen_shops else firsts).append(h)
            seen_shops.add(h.shop_id)
        kept = sorted((firsts + rest)[:limit], key=lambda h: -strength(h))
        slowest = tuple(sorted(took.items(), key=lambda x: -x[1])[:3])
        return SearchReport(kept, len(chosen), answered, failures, round(time.monotonic() - started, 2),
                            local_s, slowest)

    def is_local(self, shop) -> bool:
        """True when this shop is searched in local data (a full catalog or a sitemap index), with no request."""
        if self.local_dir is not None and (self.local_dir / f'{shop.shop_id}.json').is_file():
            return True
        return shop.platform not in ('shopify', 'woocommerce') and (shop.platform in ('merchant_feed', 'html')
                                                                    or bool(shop.feed_url))

    def warm(self) -> int:
        """Load every local index now, so the first buyer search neither pays for it nor drops shops as slow."""
        loaded = []
        for shop in self.shops:
            for folder in (self.local_dir, self.cache_dir):
                if folder is not None and (folder / f'{shop.shop_id}.json').is_file():
                    loaded.append(load_index(folder / f'{shop.shop_id}.json'))
        self.weights = WordWeights(loaded) if loaded else None
        return len(loaded)

    def rank(self, query: str, title: str) -> float:
        """How well a listing title matches a query, on one scale for live and local results."""
        return score(query, title, weight=self.weights)

    def _one(self, shop, query: str, per_shop: int, deadline) -> list[Hit]:
        if self.local_dir is not None and (self.local_dir / f'{shop.shop_id}.json').is_file():
            return self._indexed(shop, query, per_shop, self.local_dir)
        if shop.platform == 'shopify':
            client = Shopify(self.web, shop)
            if self.web.robots_for(shop.origin + '/').allows(shop.origin + '/search/suggest.json?q=x'):
                return client.search(query, limit=per_shop, deadline=deadline)
            return self._indexed(shop, query, per_shop)
        if shop.platform == 'woocommerce':
            return WooCommerce(self.web, shop).search(query, limit=per_shop, deadline=deadline)
        if shop.platform in ('merchant_feed', 'html') or shop.feed_url:
            return self._indexed(shop, query, per_shop)
        return []

    def _indexed(self, shop, query: str, per_shop: int, folder: Path | None = None) -> list[Hit]:
        index = load_index((folder or self.cache_dir) / f'{shop.shop_id}.json')
        if not index.rows:
            raise FetchError('NO_INDEX', 'this shop has no local index yet, so it was not searched')
        scored = index.best(query, per_shop, self.weights)
        hits = []
        for s, r in scored:
            if s <= 0:
                break
            hits.append(Hit(shop.shop_id, shop.name, r['title'], r['url'], money(r.get('price_min')),
                            r.get('currency') or shop.currency, r.get('available'),
                            'catalog' if folder is not None else f'index:{r.get("platform")}', match=round(s, 3)))
        return hits

    # ---- local index for shops without live search -------------------------------------------
    def index(self, shop) -> list[dict]:
        return load_index(self.cache_dir / f'{shop.shop_id}.json').rows

    def refresh(self, shop, *, max_pages: int = 30) -> dict:
        """Rebuild one shop's local index from its public catalog. Returns a small report."""
        started = time.monotonic()
        rows: list[dict] = []
        if shop.platform == 'shopify':
            client = Shopify(self.web, shop)
            client.currency()
            for page_no in range(1, max_pages + 1):
                raw = client.catalog_page(page_no)
                if not raw:
                    break
                rows.extend(client.catalog_hits(raw))
        elif shop.feed_url or shop.platform == 'html':
            if shop.feed_url:   # a merchant feed carries titles, prices and stock; a sitemap only URLs
                try:
                    rows = parse_merchant_feed(self.web.get(shop.feed_url, accept='xml', timeout=90).body, shop)
                except FetchError:
                    rows = []
            if not rows and shop.platform == 'html':
                rows = self._sitemap_rows(shop)
            if not rows:
                return dict(shop_id=shop.shop_id, status='skipped', reason='no readable feed or product sitemap')
        else:
            return dict(shop_id=shop.shop_id, status='skipped', reason='no public catalog to index')
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        payload = dict(shop_id=shop.shop_id, origin=shop.origin, fetched_at=self.now().isoformat(),
                       rows=rows)
        (self.cache_dir / f'{shop.shop_id}.json').write_text(json.dumps(payload, ensure_ascii=False),
                                                              encoding='utf-8')
        return dict(shop_id=shop.shop_id, status='ok', rows=len(rows), seconds=round(time.monotonic() - started, 1))

    def stale(self, shop) -> bool:
        path = self.cache_dir / f'{shop.shop_id}.json'
        if not path.is_file():
            return True
        try:
            fetched = datetime.fromisoformat(json.loads(path.read_text(encoding='utf-8'))['fetched_at'])
        except (ValueError, KeyError):
            return True
        return self.now() - fetched > INDEX_MAX_AGE

    def needs_index(self, shop) -> bool:
        if shop.platform == 'shopify':
            return not self.web.robots_for(shop.origin + '/').allows(shop.origin + '/search/suggest.json?q=x')
        return shop.platform == 'html' or bool(shop.feed_url)

    def _sitemap_rows(self, shop, *, max_files: int = 12, max_rows: int = 40_000) -> list[dict]:
        """Product URLs from the shop's sitemaps (robots.txt obeyed); titles come from the URL slug.

        These rows carry no price or stock: they only let the agent find a product page, which it
        then opens live for current facts."""
        robots = self.web.robots_for(shop.origin + '/')
        queue = list(robots.sitemaps) or [shop.origin + '/sitemap.xml']
        queue = [u for u in queue if u.startswith(shop.origin) or u.startswith(shop.origin.replace('://www.', '://'))]
        seen, rows, files = set(), [], 0
        while queue and files < max_files and len(rows) < max_rows:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            try:
                page = self.web.get(url, accept='xml', timeout=60)
            except FetchError:
                continue
            files += 1
            text = page.text()
            locs = SITEMAP_LOC.findall(text)
            if '<sitemapindex' in text[:2000].lower():
                children = sorted(locs, key=lambda u: (0 if 'product' in u.lower() else 1, u))
                queue.extend(children[:max_files])
                continue
            for loc in locs:
                loc = loc.strip().replace('&amp;', '&')
                if PRODUCT_PATH.search(loc) and not NOT_PRODUCT.search(loc):
                    rows.append(dict(shop_id=shop.shop_id, shop_name=shop.name, platform='sitemap',
                                     title=slug_title(loc), url=loc, vendor=None, product_type=None, tags=[],
                                     variant_titles=[], skus=[], price_min=None, available=None,
                                     currency=shop.currency))
        return rows[:max_rows]


SITEMAP_LOC = re.compile(r'<loc>\s*([^<\s]+)\s*</loc>', re.IGNORECASE)
PRODUCT_PATH = re.compile(r'/(product|products|p|item|shop)/[^/?#]+|-\d{3,}\.html$|/[^/]+-[^/]+-[^/]+\.html$',
                          re.IGNORECASE)
NOT_PRODUCT = re.compile(r'/(category|categories|collections|tag|blog|news|page|brand|brands)/', re.IGNORECASE)


def slug_title(url: str) -> str:
    slug = url.rstrip('/').rsplit('/', 1)[-1].split('?')[0]
    slug = re.sub(r'\.html?$', '', slug)
    from urllib.parse import unquote
    words = re.split(r'[-_+]+', unquote(slug))
    return ' '.join(w for w in words if w)[:240]


def main(argv: list[str]) -> int:
    web = SafeWeb(min_interval_s=0.6, per_host_cap=60, run_cap=2000, max_bytes=40 * 1024 * 1024)
    search = ShopSearch(web)
    if argv and argv[0] == 'refresh':
        wanted = set(argv[1:])
        for shop in search.shops:
            if (wanted and shop.shop_id not in wanted) or not search.needs_index(shop):
                continue
            try:
                print(json.dumps(search.refresh(shop)))
            except FetchError as exc:
                print(json.dumps(dict(shop_id=shop.shop_id, status='failed', error=exc.code)))
        return 0
    if argv and argv[0] == 'search':
        report = search.search(' '.join(argv[1:]))
        print(f'{len(report.hits)} hits from {report.shops_answered}/{report.shops_asked} shops in '
              f'{report.seconds}s; failures {report.failures}')
        for h in report.hits[:25]:
            print(f'  {h.shop_name:22} {str(h.price):>9} {h.currency or "":3} {str(h.available):5} {h.title[:70]}')
        return 0
    print('usage: python -m app.scout.catalog refresh [shop_id ...] | search <words>')
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
