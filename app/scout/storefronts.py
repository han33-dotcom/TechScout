"""Read Lebanese storefronts through the public, structured endpoints they already publish.

Most shops in the directory run Shopify or WooCommerce. Both platforms publish product
data for any visitor: Shopify at ``/products/<handle>.js``, ``/products.json`` and
``/meta.json``; WooCommerce through its Store API under ``/wp-json/wc/store/v1``. Some
shops also publish a Google Shopping feed. These answers carry the exact variant, price,
currency and stock that a scraper has to guess from rendered HTML, and they arrive in
well under a second instead of the 50 to 170 seconds measured for managed scraping.

Every read goes through ``SafeWeb`` (robots.txt, pacing, public addresses only). Nothing
here interprets the buyer's request: it only reports what the shop published.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import re
from urllib.parse import quote_plus, urlsplit
import xml.etree.ElementTree as ET

from app.scout import pages
from app.scout.web import FetchError, Page, SafeWeb

MAX_DESCRIPTION = 1500


@dataclass
class Variant:
    variant_id: str
    title: str
    options: dict[str, str] = field(default_factory=dict)
    price: Decimal | None = None
    compare_at: Decimal | None = None
    currency: str | None = None
    available: bool | None = None
    stock_text: str | None = None
    sku: str | None = None
    url: str | None = None


@dataclass
class Product:
    shop_id: str
    shop_name: str
    platform: str
    product_id: str
    title: str
    url: str
    source: str                    # which endpoint supplied the facts
    endpoint: str                  # the URL actually read
    sha256: str
    observed_at: datetime
    vendor: str | None = None
    product_type: str | None = None
    condition: str | None = None   # as published (feed g:condition, schema itemCondition, option)
    description: str = ''
    variants: list[Variant] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def compact(self) -> dict:
        """What the agent sees: published facts only, money as strings."""
        return dict(
            shop=self.shop_name, shop_id=self.shop_id, title=self.title, url=self.url,
            vendor=self.vendor, product_type=self.product_type, condition=self.condition,
            source=self.source, observed_at=self.observed_at.isoformat(timespec='seconds'),
            variants=[dict(variant_id=v.variant_id, title=v.title, options=v.options,
                           price=_s(v.price), compare_at=_s(v.compare_at), currency=v.currency,
                           available=v.available, stock_text=v.stock_text, sku=v.sku, url=v.url)
                      for v in self.variants[:40]],
            description=self.description[:MAX_DESCRIPTION], **({'extra': self.extra} if self.extra else {}))


@dataclass
class Hit:
    shop_id: str
    shop_name: str
    title: str
    url: str
    price: Decimal | None
    currency: str | None
    available: bool | None
    via: str

    def compact(self) -> dict:
        return dict(shop=self.shop_name, shop_id=self.shop_id, title=self.title, url=self.url,
                    price=_s(self.price), currency=self.currency, available=self.available, via=self.via)


def _s(value: Decimal | None) -> str | None:
    return None if value is None else format(value, 'f')


def money(value, minor_unit: int | None = None) -> Decimal | None:
    if value in (None, ''):
        return None
    try:
        amount = Decimal(str(value).replace(',', '').strip())
    except (InvalidOperation, ValueError):
        return None
    if minor_unit:
        amount = amount.scaleb(-int(minor_unit))
    return amount.quantize(Decimal('0.01')) if amount >= 0 else None


def strip_html(html: str | None, limit: int = MAX_DESCRIPTION) -> str:
    if not html:
        return ''
    return pages.parse_html(html, 'https://example.invalid/').text[:limit]


def _json(page: Page):
    try:
        return json.loads(page.body)
    except ValueError:
        raise FetchError('BAD_JSON', 'the endpoint did not return valid JSON') from None


# ------------------------------------------------------------------------------------------
# Shopify
# ------------------------------------------------------------------------------------------
class Shopify:
    platform = 'shopify'

    def __init__(self, web: SafeWeb, shop):
        self.web, self.shop = web, shop
        self._currency: str | None = getattr(shop, 'currency', None)

    def meta(self, deadline=None) -> dict:
        page = self.web.get(self.shop.origin + '/meta.json', accept='json', deadline=deadline)
        data = _json(page)
        if isinstance(data, dict):
            self._currency = data.get('currency') or self._currency
            return {k: data.get(k) for k in ('name', 'city', 'province', 'country', 'currency', 'domain')}
        return {}

    def currency(self, deadline=None) -> str | None:
        if self._currency is None:
            try:
                self.meta(deadline)
            except FetchError:
                pass
        return self._currency

    def search(self, query: str, *, limit: int = 10, deadline=None) -> list[Hit]:
        url = (f'{self.shop.origin}/search/suggest.json?q={quote_plus(query)}&resources[type]=product'
               f'&resources[limit]={min(limit, 10)}&resources[options][unavailable_products]=last'
               f'&resources[options][fields]=title,product_type,variants.title,variants.sku,vendor')
        data = _json(self.web.get(url, accept='json', deadline=deadline))
        products = (((data or {}).get('resources') or {}).get('results') or {}).get('products') or []
        cur = self.currency(deadline)
        hits = []
        for p in products:
            if not isinstance(p, dict) or not p.get('url'):
                continue
            link = self.shop.origin + str(p['url']).split('?')[0]
            hits.append(Hit(self.shop.shop_id, self.shop.name, str(p.get('title', ''))[:200], link,
                            money(p.get('price_min') or p.get('price')), cur, p.get('available'), 'shopify_suggest'))
        return hits

    def catalog_page(self, page_no: int, deadline=None) -> list[dict]:
        url = f'{self.shop.origin}/products.json?limit=250&page={int(page_no)}'
        data = _json(self.web.get(url, accept='json', deadline=deadline))
        return [p for p in (data or {}).get('products') or [] if isinstance(p, dict)]

    def catalog_hits(self, raw: list[dict]) -> list[dict]:
        """Index rows built from /products.json (title, tags, variants) for the local catalog."""
        rows = []
        for p in raw:
            variants = [v for v in p.get('variants') or [] if isinstance(v, dict)]
            prices = [money(v.get('price')) for v in variants if money(v.get('price')) is not None]
            rows.append(dict(
                shop_id=self.shop.shop_id, shop_name=self.shop.name, platform='shopify',
                title=str(p.get('title', ''))[:240], url=f"{self.shop.origin}/products/{p.get('handle')}",
                vendor=p.get('vendor'), product_type=p.get('product_type'),
                tags=[str(t) for t in (p.get('tags') or [])][:30] if isinstance(p.get('tags'), list)
                else str(p.get('tags') or '')[:300],
                variant_titles=[str(v.get('title', ''))[:80] for v in variants][:30],
                skus=[str(v.get('sku') or '')[:60] for v in variants if v.get('sku')][:30],
                price_min=_s(min(prices)) if prices else None,
                available=any(v.get('available') for v in variants) if variants and
                any('available' in v for v in variants) else None,
                currency=self._currency))
        return rows

    def product(self, url: str, deadline=None) -> Product:
        handle = _shopify_handle(url)
        if not handle:
            raise FetchError('NOT_PRODUCT', 'not a Shopify product URL')
        endpoint = f'{self.shop.origin}/products/{handle}.js'
        page = self.web.get(endpoint, accept='json', deadline=deadline)
        p = _json(page)
        if not isinstance(p, dict) or 'variants' not in p:
            raise FetchError('NOT_PRODUCT', 'the shop did not return a product')
        cur = self.currency(deadline)
        names = [o.get('name') if isinstance(o, dict) else str(o) for o in p.get('options') or []]
        variants = []
        for v in p.get('variants') or []:
            opts = {}
            for i, name in enumerate(names[:3], start=1):
                value = v.get(f'option{i}')
                if value is not None:
                    opts[str(name)] = str(value)
            variants.append(Variant(
                variant_id=str(v.get('id')), title=str(v.get('title', ''))[:160], options=opts,
                price=money(v.get('price'), 2), compare_at=money(v.get('compare_at_price'), 2), currency=cur,
                available=v.get('available') if isinstance(v.get('available'), bool) else None,
                sku=(str(v['sku'])[:80] if v.get('sku') else None),
                url=f"{self.shop.origin}/products/{handle}?variant={v.get('id')}"))
        return Product(self.shop.shop_id, self.shop.name, 'shopify', str(p.get('id')), str(p.get('title', ''))[:240],
                       f'{self.shop.origin}/products/{handle}', 'shopify_product_js', endpoint, page.sha256,
                       page.observed_at, vendor=p.get('vendor'), product_type=p.get('type'),
                       description=strip_html(p.get('description')), variants=variants)


def _shopify_handle(url: str) -> str | None:
    m = re.search(r'/products/([A-Za-z0-9%._~-]+)', urlsplit(url).path)
    if not m:
        return None
    handle = m.group(1)
    for suffix in ('.js', '.json', '.oembed'):
        if handle.endswith(suffix):
            handle = handle[: -len(suffix)]
    return handle[:255] or None


# ------------------------------------------------------------------------------------------
# WooCommerce Store API
# ------------------------------------------------------------------------------------------
class WooCommerce:
    platform = 'woocommerce'
    BASE = '/wp-json/wc/store/v1/products'

    def __init__(self, web: SafeWeb, shop):
        self.web, self.shop = web, shop

    def search(self, query: str, *, limit: int = 12, deadline=None) -> list[Hit]:
        url = f'{self.shop.origin}{self.BASE}?search={quote_plus(query)}&per_page={min(limit, 20)}'
        data = _json(self.web.get(url, accept='json', deadline=deadline))
        hits = []
        for p in data if isinstance(data, list) else []:
            if not isinstance(p, dict) or not p.get('permalink'):
                continue
            prices = p.get('prices') or {}
            hits.append(Hit(self.shop.shop_id, self.shop.name, _unescape(p.get('name', ''))[:200], p['permalink'],
                            money(prices.get('price'), prices.get('currency_minor_unit')),
                            prices.get('currency_code'), p.get('is_in_stock'), 'woo_store_api'))
        return hits

    def product(self, url: str, deadline=None) -> Product:
        slug = urlsplit(url).path.rstrip('/').rsplit('/', 1)[-1]
        if not slug:
            raise FetchError('NOT_PRODUCT', 'not a product URL')
        endpoint = f'{self.shop.origin}{self.BASE}?slug={quote_plus(slug)}'
        page = self.web.get(endpoint, accept='json', deadline=deadline)
        data = _json(page)
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            raise FetchError('NOT_PRODUCT', 'the shop did not return a product for this URL')
        p = data[0]
        variants = []
        if p.get('type') == 'variable' and p.get('variations'):
            for ref in p['variations'][:24]:
                vid = ref.get('id')
                opts = {a.get('name', ''): a.get('value', '') for a in ref.get('attributes') or [] if isinstance(a, dict)}
                try:
                    vp = _json(self.web.get(f'{self.shop.origin}{self.BASE}/{int(vid)}', accept='json',
                                            deadline=deadline))
                except (FetchError, TypeError, ValueError):
                    variants.append(Variant(str(vid), ' / '.join(opts.values()), opts))
                    continue
                variants.append(_woo_variant(vp, opts))
        else:
            variants.append(_woo_variant(p, {}))
        attrs = {a.get('name'): [t.get('name') for t in a.get('terms') or []][:12]
                 for a in p.get('attributes') or [] if isinstance(a, dict)}
        description = strip_html((p.get('short_description') or '') + '\n' + (p.get('description') or ''))
        return Product(self.shop.shop_id, self.shop.name, 'woocommerce', str(p.get('id')),
                       _unescape(p.get('name', ''))[:240], p.get('permalink') or url, 'woo_store_api', endpoint,
                       page.sha256, page.observed_at, description=description, variants=variants,
                       extra={'attributes': attrs} if attrs else {})


def _woo_variant(p: dict, opts: dict) -> Variant:
    prices = p.get('prices') or {}
    stock = p.get('stock_availability') or {}
    if not opts and p.get('variation'):
        opts = {'variation': str(p['variation'])[:120]}
    return Variant(variant_id=str(p.get('id')), title=_unescape(p.get('variation') or p.get('name') or '')[:160],
                   options=opts, price=money(prices.get('price'), prices.get('currency_minor_unit')),
                   compare_at=money(prices.get('regular_price'), prices.get('currency_minor_unit'))
                   if prices.get('regular_price') != prices.get('price') else None,
                   currency=prices.get('currency_code'),
                   available=p.get('is_in_stock') if isinstance(p.get('is_in_stock'), bool) else None,
                   stock_text=str(stock.get('text'))[:80] if stock.get('text') else None,
                   sku=(str(p['sku'])[:80] if p.get('sku') else None), url=p.get('permalink'))


def _unescape(text: str) -> str:
    import html
    return html.unescape(str(text or ''))


# ------------------------------------------------------------------------------------------
# Google Shopping merchant feed (for example Technodel's /api/merchant-feed)
# ------------------------------------------------------------------------------------------
G = '{http://base.google.com/ns/1.0}'


def parse_merchant_feed(body: bytes, shop) -> list[dict]:
    rows = []
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return rows
    for item in root.iter('item'):
        def g(name):
            node = item.find(G + name)
            return (node.text or '').strip() if node is not None and node.text else None
        title = (item.findtext('title') or '').strip()
        link = (item.findtext('link') or '').strip()
        if not title or not link.startswith('https://'):
            continue
        price_text = g('sale_price') or g('price') or ''
        m = re.match(r'\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*([A-Z]{3})?', price_text)
        rows.append(dict(shop_id=shop.shop_id, shop_name=shop.name, platform='merchant_feed', title=title[:240],
                         url=link, vendor=g('brand'), product_type=g('product_type') or g('google_product_category'),
                         tags=[], variant_titles=[], skus=[s for s in (g('mpn'), g('gtin'), g('id')) if s],
                         price_min=_s(money(m.group(1))) if m else None, currency=(m.group(2) if m else None),
                         available=(g('availability') or '').lower().replace('_', ' ') == 'in stock'
                         if g('availability') else None,
                         condition=g('condition'), description=strip_html(item.findtext('description'), 400)))
    return rows


# ------------------------------------------------------------------------------------------
# Any product page: schema.org JSON-LD, OpenGraph and visible text
# ------------------------------------------------------------------------------------------
def product_from_html(page: Page, shop) -> tuple[Product | None, pages.ParsedPage]:
    parsed = pages.parse_html(page.text(), page.final_url)
    found = pages.jsonld_products(parsed.jsonld)
    variants, title, condition, vendor = [], parsed.title, None, None
    for item in found:
        name = str(item.get('name') or '')[:200]
        title = title or name
        brand = item.get('brand')
        vendor = vendor or (brand.get('name') if isinstance(brand, dict) else (str(brand) if brand else None))
        for i, offer in enumerate(pages.offers_of(item)):
            availability = pages.schema_value(offer.get('availability'))
            cond = pages.schema_value(offer.get('itemCondition') or item.get('itemCondition')) or None
            condition = condition or cond
            variants.append(Variant(
                variant_id=str(offer.get('sku') or item.get('sku') or f'offer-{len(variants)}')[:80],
                title=name or title, options={k: str(item[k])[:60] for k in ('color', 'size', 'material') if item.get(k)},
                price=money(offer.get('price') or (offer.get('priceSpecification') or {}).get('price')
                            if isinstance(offer.get('priceSpecification') or {}, dict) else offer.get('price')),
                currency=offer.get('priceCurrency'), available=(availability in ('InStock', 'LimitedAvailability',
                                                                                 'OnlineOnly', 'InStoreOnly')
                                                                 if availability else None),
                stock_text=availability or None, sku=str(offer.get('sku') or item.get('sku') or '')[:80] or None,
                url=offer.get('url') if isinstance(offer.get('url'), str) else None))
    if not variants:
        amount = parsed.meta.get('product:price:amount') or parsed.meta.get('og:price:amount')
        if amount:
            variants.append(Variant('og', parsed.meta.get('og:title', title)[:160], {}, money(amount),
                                    currency=parsed.meta.get('product:price:currency') or parsed.meta.get('og:price:currency'),
                                    available=None, stock_text=parsed.meta.get('product:availability')))
    if not variants:
        return None, parsed
    return Product(shop.shop_id, shop.name, getattr(shop, 'platform', 'html'), variants[0].variant_id, title,
                   page.final_url, 'schema_org_jsonld' if found else 'opengraph', page.final_url, page.sha256,
                   page.observed_at, vendor=vendor, condition=condition,
                   description=parsed.meta.get('description', '')[:600], variants=variants), parsed


def as_dict(obj) -> dict:
    return asdict(obj)
