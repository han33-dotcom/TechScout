"""The scout agent's tools. Each returns a compact, JSON-safe result the model reads next turn.

Tool results are data. Page text is wrapped and labelled as retailer content, and the
agent is told never to follow instructions found inside it.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import re
import threading
import time
from urllib.parse import urlsplit

from app.scout import pages
from app.scout.catalog import ShopSearch
from app.scout.evidence import Evidence, product_text
from app.scout.storefronts import Shopify, WooCommerce, product_from_html
from app.scout.terms import live_terms
from app.scout.web import FetchError, SafeWeb

PAGE_CHARS = 3500
TERMS_HINT = re.compile(r'deliver|shipping|ship|fee|charge|vat|tax|cash on|location|address|branch|store|contact|'
                        r'warranty|return|توصيل|شحن', re.IGNORECASE)
POLICY_LINK = r'shipping|delivery|faq|contact|about|terms|policy|policies|location|branches|stores|warranty'


class Tools:
    def __init__(self, web: SafeWeb, search: ShopSearch, evidence: Evidence, *, shops=None,
                 extra_hosts: set[str] | None = None):
        self.web, self.search_engine, self.evidence = web, search, evidence
        self.shops = {s.shop_id: s for s in (shops if shops is not None else search.shops)}
        self.by_host = {}
        for s in self.shops.values():
            self.by_host[s.host.lower()] = s
            self.by_host[s.host.lower().removeprefix('www.')] = s
        self.extra_hosts = {h.lower() for h in (extra_hosts or set())}
        self.calls: list[dict] = []
        self.deadline: float | None = None
        self._terms: dict[str, dict | None] = {}
        self._terms_lock = threading.Lock()
        self._opened: dict[str, str] = {}   # product URL (no query) -> observation ID

    # ---- dispatch ----------------------------------------------------------------------------
    def run_many(self, calls: list[dict]) -> list[dict]:
        with ThreadPoolExecutor(max_workers=6) as pool:
            return list(pool.map(self.run_one, calls))

    def run_one(self, call: dict) -> dict:
        tool = call.get('tool')
        started = time.monotonic()
        try:
            if tool == 'search':
                out = self.search(call.get('query') or '', call.get('shop_ids') or None)
            elif tool == 'open':
                out = self.open(call.get('url') or '')
            elif tool == 'read':
                out = self.read(call.get('url') or '', call.get('terms') or [])
            elif tool == 'shop_info':
                out = self.shop_info((call.get('shop_ids') or [None])[0])
            else:
                out = dict(error='unknown tool')
        except FetchError as exc:
            out = dict(error=exc.code, reason=exc.reason)
        except Exception as exc:  # noqa: BLE001 - a tool failure is reported, never fatal
            out = dict(error='tool_failure', reason=type(exc).__name__)
        self.calls.append(dict(tool=tool, ms=round((time.monotonic() - started) * 1000),
                               ok='error' not in out, error=out.get('error')))
        return dict(call=_echo(call), result=out)

    # ---- tools -------------------------------------------------------------------------------
    def search(self, query: str, shop_ids: list[str] | None) -> dict:
        query = ' '.join(query.split())[:120]
        if len(query) < 2:
            return dict(error='empty_query', reason='write a short product query, e.g. "galaxy s24 ultra 256"')
        report = self.search_engine.search(query, shop_ids=shop_ids, deadline=self.deadline)
        hits = report.hits[:15]
        out = dict(query=query, shops_answered=report.shops_answered, shops_asked=report.shops_asked,
                   failed_shops=sorted(report.failures), seconds=report.seconds,
                   hits=[dict(shop=h.shop_name, title=h.title, price=_money(h.price), cur=h.currency,
                              in_stock=h.available, url=h.url) for h in hits],
                   note='Leads only. The best matches are already opened below (details); open others if needed.')
        out['details'] = self.preopen(query, hits)
        return out

    def preopen(self, query: str, hits, *, k: int = 3) -> list[dict]:
        """Open the strongest few leads at once (free, ~1 s) so the agent can often skip an 'open' turn."""
        from app.scout.catalog import score
        chosen, seen = [], set()
        from app.scout.verify import USED
        # Strongest text match first; listings marked used or open box after new ones; in stock first.
        for h in sorted(hits, key=lambda h: (-round(score(query, h.title), 2), bool(USED.search(h.title)),
                                             h.available is False)):
            key = h.url.split('?')[0]
            if score(query, h.title) < 0.6 or key in seen or key in self._opened:
                continue
            seen.add(key)
            chosen.append(h.url)
            if len(chosen) == k:
                break
        if not chosen:
            return []
        from concurrent.futures import wait
        pool = ThreadPoolExecutor(max_workers=k)
        futures = [pool.submit(self._safe_open, url) for url in chosen]
        done, _ = wait(futures, timeout=10)
        pool.shutdown(wait=False, cancel_futures=True)
        results = [f.result() for f in futures if f in done]
        return [_compact_product(r) for r in results if r and 'error' not in r]

    def _safe_open(self, url: str) -> dict | None:
        try:
            return self.open(url)
        except FetchError as exc:
            return dict(error=exc.code)
        except Exception:  # noqa: BLE001
            return None

    def _shop_for(self, url: str):
        host = (urlsplit(url).hostname or '').lower()
        return self.by_host.get(host) or self.by_host.get(host.removeprefix('www.'))

    def _check_scope(self, url: str):
        shop = self._shop_for(url)
        host = (urlsplit(url).hostname or '').lower()
        if shop is None and host not in self.extra_hosts:
            raise FetchError('OUT_OF_DIRECTORY', 'this host is not in the reviewed Lebanese shop directory')
        if shop is not None and not shop.enabled:
            raise FetchError('SHOP_DISABLED', f'{shop.name} is disabled in the directory: {shop.notes[:120]}')
        return shop

    def open(self, url: str) -> dict:
        shop = self._check_scope(url)
        product = None
        if shop is not None and shop.platform == 'shopify' and '/products/' in url:
            product = Shopify(self.web, shop).product(url, deadline=self.deadline)
        elif shop is not None and shop.platform == 'woocommerce':
            try:
                product = WooCommerce(self.web, shop).product(url, deadline=self.deadline)
            except FetchError:
                product = None
        if product is None:
            page = self.web.get(url, accept='html', deadline=self.deadline)
            product, parsed = product_from_html(page, shop or _Anon(url))
            if product is None:
                obs = self.evidence.add('T', kind='page', url=page.final_url, shop_id=getattr(shop, 'shop_id', None),
                                        observed_at=page.observed_at, sha256=page.sha256, text=parsed.text,
                                        meta=dict(title=parsed.title))
                return dict(obs=obs.obs_id, kind='page', note='No structured product data; read the text.',
                            title=parsed.title, url=page.final_url, text=_window(parsed.text, 0, PAGE_CHARS))
        obs = self.evidence.add('P', kind='product', url=product.url, shop_id=product.shop_id,
                                observed_at=product.observed_at, sha256=product.sha256,
                                text=product_text(product), product=product)
        self._opened[product.url.split('?')[0]] = obs.obs_id
        shown = product.compact()
        out = dict(obs=obs.obs_id, kind='product', **shown)
        if shop is not None:
            terms = self.terms_for(shop)
            if terms:
                out['shop_terms'] = terms
        return out

    def terms_for(self, shop) -> dict | None:
        """The shop's published delivery/VAT/address terms, re-read once per run (current receipts)."""
        with self._terms_lock:
            if shop.shop_id in self._terms:
                cached = self._terms[shop.shop_id]
                return dict(cached, note='same terms as shown earlier in this run') if cached else None
            self._terms[shop.shop_id] = None
        found = live_terms(self.web, self.evidence, shop, deadline=self.deadline)
        with self._terms_lock:
            self._terms[shop.shop_id] = found
        return found

    def read(self, url: str, terms: list[str]) -> dict:
        shop = self._check_scope(url)
        page = self.web.get(url, accept='any', deadline=self.deadline)
        if page.content_type.endswith('json'):
            text = page.text()[:60000]
            parsed = None
        else:
            parsed = pages.parse_html(page.text(), page.final_url)
            text = parsed.text
        obs = self.evidence.add('T', kind='page', url=page.final_url, shop_id=getattr(shop, 'shop_id', None),
                                observed_at=page.observed_at, sha256=page.sha256, text=text,
                                meta=dict(title=parsed.title if parsed else ''))
        terms = [t for t in terms if isinstance(t, str)][:8]
        found = pages.snippets(text, terms) if terms else []
        out = dict(obs=obs.obs_id, kind='page', url=page.final_url, title=parsed.title if parsed else '',
                   chars=len(text), snippets=found)
        if not found:
            out['text'] = _window(text, 0, PAGE_CHARS)
        if parsed:
            out['policy_links'] = pages.same_site_links(parsed, page.final_url, pattern=POLICY_LINK, limit=15)
        return out

    def shop_info(self, shop_id: str | None) -> dict:
        shop = self.shops.get(shop_id or '')
        if shop is None:
            return dict(error='unknown_shop', known=sorted(self.shops)[:60])
        info = dict(shop_id=shop.shop_id, name=shop.name, origin=shop.origin, platform=shop.platform,
                    directory_country=shop.country, directory_city=shop.city, provenance=shop.provenance,
                    notes=shop.notes)
        facts = []
        if shop.platform == 'shopify':
            try:
                meta = Shopify(self.web, shop).meta(deadline=self.deadline)
                page_text = json.dumps(meta, ensure_ascii=False)
                obs = self.evidence.add('T', kind='page', url=shop.origin + '/meta.json', shop_id=shop.shop_id,
                                        observed_at=_now(), sha256='', text=page_text, meta=dict(title='meta.json'))
                facts.append(dict(obs=obs.obs_id, source='shop meta.json', data=meta))
            except FetchError as exc:
                facts.append(dict(source='shop meta.json', error=exc.code))
        if shop.shop_id == 'technodel':
            try:
                page = self.web.get(shop.origin + '/api/llm-context', accept='json', deadline=self.deadline)
                data = json.loads(page.body).get('_metadata', {})
                keep = {k: data.get(k) for k in ('site', 'stores', 'delivery', 'warranty', 'returns', 'currency')}
                text = json.dumps(keep, ensure_ascii=False)
                obs = self.evidence.add('T', kind='page', url=page.final_url, shop_id=shop.shop_id,
                                        observed_at=page.observed_at, sha256=page.sha256, text=text,
                                        meta=dict(title='llm-context'))
                facts.append(dict(obs=obs.obs_id, source='technodel /api/llm-context', data=keep))
            except (FetchError, ValueError) as exc:
                facts.append(dict(source='llm-context', error=getattr(exc, 'code', 'bad_json')))
        try:
            page = self.web.get(shop.origin + '/', accept='html', deadline=self.deadline)
            parsed = pages.parse_html(page.text(), page.final_url)
            links = pages.same_site_links(parsed, page.final_url, pattern=POLICY_LINK, limit=20)
            lines = [ln for ln in parsed.text.split('\n') if TERMS_HINT.search(ln) and len(ln) < 300][:25]
            obs = self.evidence.add('T', kind='page', url=page.final_url, shop_id=shop.shop_id,
                                    observed_at=page.observed_at, sha256=page.sha256, text=parsed.text,
                                    meta=dict(title=parsed.title))
            facts.append(dict(obs=obs.obs_id, source='home page', delivery_and_store_lines=lines,
                              policy_links=links))
        except FetchError as exc:
            facts.append(dict(source='home page', error=exc.code))
        info['facts'] = facts
        return info


class _Anon:
    def __init__(self, url):
        self.shop_id = (urlsplit(url).hostname or 'unknown')
        self.name = self.shop_id
        self.platform = 'html'


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def _money(value):
    return None if value is None else format(value, 'f')


def _compact_product(r: dict) -> dict:
    """A product result without the bulk: up to 12 variants, 400 characters of description."""
    keep = {k: r.get(k) for k in ('obs', 'shop', 'shop_id', 'title', 'url', 'vendor', 'condition', 'source')}
    keep['variants'] = [{k: v.get(k) for k in ('variant_id', 'title', 'price', 'currency', 'available', 'sku')
                         if v.get(k) not in (None, '')} for v in (r.get('variants') or [])[:12]]
    keep['description'] = (r.get('description') or '')[:400]
    if r.get('shop_terms'):
        keep['shop_terms'] = r['shop_terms']
    return {k: v for k, v in keep.items() if v not in (None, '', [])}


def _window(text: str, start: int, size: int) -> str:
    return text[start:start + size]


def _echo(call: dict) -> dict:
    return {k: v for k, v in call.items() if v not in (None, [], '')}
