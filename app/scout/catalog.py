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

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
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


def score(query: str, title: str, extra: str = '') -> float:
    """Share of the query's distinctive words that appear in the title (extra text counts half),
    scaled down for accessory listings the buyer did not ask for."""
    q = [t for t in tokens(query) if t not in STOP] or tokens(query)
    if not q:
        return 0.0
    words, more = set(tokens(title)), set(tokens(extra))
    joined = ''.join(tokens(title))
    got = 0.0
    for t in q:
        if t in words or (len(t) >= 4 and t in joined):
            got += 1
        elif t in more:
            got += 0.5
    return got / len(q) * accessory_penalty(query, title)


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

    def best(self, query: str, k: int) -> list[tuple[float, dict]]:
        q = [t for t in tokens(query) if t not in STOP] or tokens(query)
        if not q:
            return []
        wanted = set(tokens(query))
        candidates = set()
        for t in q:
            candidates.update(self.lookup.get(t, ()))
        scored = []
        for i in candidates:
            words, joined, more = self.title_words[i], self.joined[i], self.more_words[i]
            got = 0.0
            for t in q:
                if t in words or (len(t) >= 4 and t in joined):
                    got += 1
                elif t in more:
                    got += 0.5
            extra = (words & ACCESSORY) - wanted
            penalty = 1.0 if not extra else (0.35 if 'for' in words or 'compatible' in words or len(extra) >= 2
                                             else 0.6)
            scored.append((got / len(q) * penalty, self.rows[i]))
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


class ShopSearch:
    def __init__(self, web: SafeWeb, shops: list | None = None, *, cache_dir: Path = CACHE_DIR,
                 now=lambda: datetime.now(timezone.utc), workers: int = 12):
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
        wait_s = SEARCH_SECONDS if deadline is None else max(1.0, min(SEARCH_SECONDS, deadline - time.monotonic()))
        pool = ThreadPoolExecutor(max_workers=self.workers)
        futures = {pool.submit(self._one, s, query, per_shop, deadline): s for s in chosen}
        done, late = wait(futures, timeout=wait_s)
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
        ranked = sorted(results, key=lambda h: (-score(query, h.title), h.available is False,
                                                h.price if h.price is not None else Decimal('1e9')))
        kept = [h for h in ranked if score(query, h.title) > 0][:limit]
        return SearchReport(kept, len(chosen), answered, failures, round(time.monotonic() - started, 2))

    def _one(self, shop, query: str, per_shop: int, deadline) -> list[Hit]:
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

    def _indexed(self, shop, query: str, per_shop: int) -> list[Hit]:
        scored = load_index(self.cache_dir / f'{shop.shop_id}.json').best(query, per_shop)
        hits = []
        for s, r in scored:
            if s <= 0:
                break
            hits.append(Hit(shop.shop_id, shop.name, r['title'], r['url'], money(r.get('price_min')),
                            r.get('currency') or shop.currency, r.get('available'), f'index:{r.get("platform")}'))
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
        elif shop.platform == 'html':
            rows = self._sitemap_rows(shop)
            if not rows:
                return dict(shop_id=shop.shop_id, status='skipped', reason='no product sitemap found')
        elif shop.feed_url:
            page = self.web.get(shop.feed_url, accept='xml', timeout=90)
            rows = parse_merchant_feed(page.body, shop)
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
