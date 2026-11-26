"""Class Cellulars variable-phone discovery and policy reader.

The product page states VAT inclusion next to the price, a 14-30 day delivery estimate, an
18-month warranty at Genuine Warranty Center and a free-shipping banner. The FAQ states delivery
times for Beirut and outside it, cash-on-delivery-only payment and a three-day return window. The
delivery time can conflict with the product page's estimate; apply_policy keeps both. The FAQ
speaks to online (delivery) orders, so it settles nothing for a pickup offer.
"""
from decimal import Decimal
import re
from types import MappingProxyType
from urllib.parse import quote

from app.adapters.base import Snapshot, parse_offer_id
from app.adapters.woo_catalog import WooCatalog
from app.adapters.woocommerce import strict_json, _Read
from app.devices import match_model
from app.source_capabilities import CapabilityRef, safe_endpoint


from app.adapters.base import PolicyDocument, PolicyFact
from app.adapters.woocommerce import (
    ARRIVAL_CONFLICT, MAX_POLICY_DAYS, PAYMENT_CONFLICT, Claim, PageFacts, PolicyPage, ProductEndpoints,
    StoreConfig, WooCommerceAdapter, decimal_amount, merge_policy_fact, policy_window,
)
from app.contracts import Offer

SOURCE_ID = 'class_cellulars'
ORIGIN = 'https://classcellulars.com'
MOBILE_CATEGORY_ID = 132
MAX_VAT_CLAIMS = 50


def phone_search_url(term):
    return (f'{ORIGIN}/wp-json/wc/store/v1/products?search={quote(term)}'
            f'&category={MOBILE_CATEGORY_ID}&per_page=10&page=1')


PHONE_LINKS = WooCatalog(SOURCE_ID, ORIGIN, {'phone': ('mobiles', 'smartphones', 'mobile-phones')},
                        [('product', ('product',), 2)])

# Prices read '1,030.00 $ (Inc. VAT)'; the left boundary stops a match inside a longer number.
_PRICE_VAT = re.compile(r'(?<![0-9.,])([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(\.[0-9]{1,6})?\s*\$\s*'
                        r'\(\s*Inc\.\s*VAT\s*\)', re.IGNORECASE)
_ESTIMATE = re.compile(r'Estimated\s+delivery\s+time\s+([0-9]{1,3})\s*[-\u2013]\s*([0-9]{1,3})\s+days\b',
                       re.IGNORECASE)
_WARRANTY = re.compile(r'(?<![0-9])([0-9]{1,3})\s+months?\s+warranty\s+at\s+'
                       r'Genuine\s+Warranty\s+Cent(?:er|re)\b', re.IGNORECASE)
# '$1,500' is written with thousands separators, as the store writes its prices. A number that goes
# on ('$1,50', '$50.505') is not this pattern's, so the banner is not read rather than cut short.
_BANNER = re.compile(r'Free\s+Shipping\s+On\s+All\s+Orders\s+Over\s+\$\s*'
                     r'((?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]{1,6})(?:\.[0-9]{1,2})?)(?![0-9]|[.,][0-9])',
                     re.IGNORECASE)
# min_subtotal means at least; in a two-decimal currency, over $50 is at least $50.01.
_CENT = Decimal('0.01')
_BEIRUT = re.compile(r'Within\s+Beirut\s*:\s*delivery\s+takes\s+([0-9]{1,2})\s+working\s+days?\b',
                     re.IGNORECASE)
_OUTSIDE = re.compile(r'Outside\s+Beirut\s*:\s*delivery\s+takes\s+up\s+to\s+([0-9]{1,2})\s+working\s+days?\b',
                      re.IGNORECASE)
_CASH_ONLY = re.compile(r'we\s+only\s+accept\s+cash\s+on\s+delivery\b', re.IGNORECASE)
_RETURNS = re.compile(r'request\s+a\s+return\s+within\s+([0-9]{1,2})\s+days?\b', re.IGNORECASE)


def price_tax_claims(text: str) -> tuple[Claim, ...]:
    """Read inclusion only from the target product's price block."""
    return tuple(Claim(decimal_amount(m.group(1) + (m.group(2) or '')), m.group(0))
                 for m in _PRICE_VAT.finditer(text))[:MAX_VAT_CLAIMS]


def page_facts(text: str) -> PageFacts:
    """The product page's own statements; the free-shipping banner is site-wide."""
    estimate, warranty, banner = _ESTIMATE.search(text), _WARRANTY.search(text), _BANNER.search(text)
    arrival = None
    if estimate is not None and int(estimate.group(1)) <= int(estimate.group(2)):
        arrival = Claim((int(estimate.group(1)), int(estimate.group(2)), 'calendar'), estimate.group(0))
    return PageFacts(
        arrival=arrival,
        warranty=None if warranty is None else Claim(
            ('Genuine Warranty Center', int(warranty.group(1))), warranty.group(0)),
        delivery_fee=None if banner is None else Claim(
            {'amount': Decimal('0'), 'currency': 'USD',
             'conditions': (f'min_subtotal:USD:{decimal_amount(banner.group(1)) + _CENT}',)},
            banner.group(0), scope='source'))


def faq_facts(text: str) -> tuple[PolicyFact, ...]:
    """Delivery time per zone, payment and returns, each with the sentence that states it."""
    facts = []
    if (m := _BEIRUT.search(text)) and int(m.group(1)) <= MAX_POLICY_DAYS:
        days = int(m.group(1))
        facts.append(PolicyFact('delivery_time', {'zone': 'Beirut', 'min_days': days, 'max_days': days,
                                                  'unit': 'working'}, m.group(0)))
    if (m := _OUTSIDE.search(text)) and int(m.group(1)) <= MAX_POLICY_DAYS:
        value = {'zone': 'outside Beirut', 'min_days': 0, 'max_days': int(m.group(1)), 'unit': 'working'}
        facts.append(PolicyFact('delivery_time', value, m.group(0)))
    if m := _CASH_ONLY.search(text):
        facts.append(PolicyFact('payment', {'methods': ['cash'], 'only': True}, m.group(0)))
    if m := _RETURNS.search(text):
        facts.append(PolicyFact('returns', {'days': int(m.group(1))}, m.group(0)))
    return tuple(facts)


def _zone(city: str) -> str:
    return 'Beirut' if city.strip().casefold() == 'beirut' else 'outside Beirut'


def apply_faq(offer: Offer, policy: PolicyDocument) -> Offer:
    """Cash on delivery settles a delivery offer's payment basis; the zone's delivery time joins the
    page's estimate. The FAQ answers for online orders only, so a pickup offer keeps both unknown."""
    if policy.policy_id != 'faq' or offer.fulfillment.mode != 'delivery':
        return offer
    for fact in policy.facts:
        value = fact.value
        if fact.topic == 'payment' and value.get('only') is True and value.get('methods') == ['cash']:
            offer = merge_policy_fact(offer, policy, fact, 'payment_basis', 'cash',
                                      conflict_reason=PAYMENT_CONFLICT)
        elif fact.topic == 'delivery_time' and value.get('zone') == _zone(offer.fulfillment.city):
            window = policy_window(policy.observed_at, value)
            if window is not None:
                offer = merge_policy_fact(offer, policy, fact, 'arrival', window,
                                          conflict_reason=ARRIVAL_CONFLICT)
    return offer


CONFIG = StoreConfig(
    source_id=SOURCE_ID,
    adapter_version='class_cellulars/1',
    products=(ProductEndpoints(
        key='18392', api_url=f'{ORIGIN}/wp-json/wc/store/v1/products?slug=iphone-17',
        page_url=f'{ORIGIN}/product/iphone-17/', label='iPhone 17'),),
    policies=(PolicyPage('faq', f'{ORIGIN}/faq/', ('payment_basis', 'arrival'), faq_facts,
                         delivery_only=('payment_basis', 'arrival')),),
    sim_map=MappingProxyType({'nano sim / esim': 'physical+esim', 'esim': 'esim-only'}),
    page_facts=page_facts,
    price_tax_claims=price_tax_claims,
    policy_effect=apply_faq,
    # The FAQ's store list, observed 2026-11-14.
    branches=(('mar-elias', 'Beirut'), ('abc-verdun', 'Beirut'), ('abc-achrafieh', 'Beirut'),
              ('abc-dbayeh', 'Dbayeh'), ('tyre', 'Tyre')),
)


class ClassCatalogAdapter(WooCommerceAdapter):
    """Bounded variable-phone discovery; other categories remain unsupported.

    No process-global discovery cache: refresh rediscovers the same model using
    the current run's gate and verifies the parent/variant IDs again.
    """
    supported_categories = ('phone',)
    max_products = 2

    def _scoped(self, fetch):
        def scoped(source_id, url, *, expect):
            if url in {p.api_url for p in CONFIG.products} | {p.page_url for p in CONFIG.products}:
                return fetch(source_id, url, expect=expect)
            kind = 'class-product' if expect == 'html' else 'class-slug'
            return fetch(source_id, url, expect=expect,
                         capability=CapabilityRef(capability_id=kind, version='1'))
        return scoped

    def _discover(self, fetch, query):
        read = _Read()
        model = match_model(query.model)
        if model is None:
            read.errors.append(self._error('unsupported phone model'))
            return (), read
        # An unfiltered first page can be entirely accessories, even for an exact model on the site.
        url = phone_search_url(model)
        outcome = fetch(self.source_id, url, expect='json',
                        capability=CapabilityRef(capability_id='class-search', version='2'))
        read.outcomes.append(outcome)
        if outcome.errors or outcome.body is None:
            return (), read
        read.snapshots.append(Snapshot.of(outcome))
        try:
            records = strict_json(outcome.body.decode('utf-8'))
            if not isinstance(records, list) or len(records) > 10:
                raise ValueError()
        except (ValueError, UnicodeDecodeError, RecursionError):
            read.errors.append(self._error('invalid catalog response'))
            return (), read
        products = {}
        for record in records:
            if not isinstance(record, dict):
                read.errors.append(self._error('invalid catalog entry'))
                continue
            # A case/accessory mentioning a phone is never a phone candidate.
            categories = record.get('categories', [])
            is_phone = isinstance(categories, list) and any(
                isinstance(c, dict) and c.get('slug') in ('mobiles', 'smartphones', 'mobile-phones')
                for c in categories)
            name, slug, pid = record.get('name'), record.get('slug'), record.get('id')
            if not (is_phone and record.get('type') == 'variable' and record.get('parent') == 0
                    and isinstance(name, str) and match_model(name) == model):
                continue
            if not (type(pid) is int and 0 < pid < 10**12 and isinstance(slug, str)
                    and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,99}', slug)):
                read.errors.append(self._error('invalid catalog identity'))
                continue
            page = f'{ORIGIN}/product/{slug}/'
            if record.get('permalink') != page or not safe_endpoint(page, 'classcellulars.com'):
                read.errors.append(self._error('unsafe catalog product URL'))
                continue
            product = ProductEndpoints(str(pid), f'{ORIGIN}/wp-json/wc/store/v1/products?slug={slug}', page, name)
            if str(pid) in products and products[str(pid)] != product:
                read.errors.append(self._error('conflicting catalog identity'))
                return (), read
            products[str(pid)] = product
        if len(products) > self.max_products or len(records) == 10:
            read.errors.append(self._error('catalog discovery truncated by configured bound'))
        return tuple(products.values())[:self.max_products], read

    def search_candidates(self, fetch, query):
        if match_model(query.model) == 'iPhone 17':
            return super().search_candidates(fetch, query)
        products, discovery = self._discover(fetch, query)
        return self._result([discovery] + [self._read(self._scoped(fetch), p, query) for p in products])

    def fetch_offers(self, fetch, offer_ids, query):
        if match_model(query.model) == "iPhone 17":
            return super().fetch_offers(fetch, offer_ids, query)
        ids = offer_ids if isinstance(offer_ids, (list, tuple)) else []
        try:
            parsed = [parse_offer_id(value) for value in ids]
        except (ValueError, TypeError, AttributeError):
            return super().fetch_offers(fetch, ids, query)
        if not parsed or any(s != self.source_id for s, _, _ in parsed):
            return super().fetch_offers(fetch, ids, query)
        if all(p in self._products for _, p, _ in parsed):
            return super().fetch_offers(fetch, ids, query)
        products, discovery = self._discover(fetch, query)
        available = {p.key: p for p in products + self.config.products}
        wanted = {}
        for _, pid, vid in parsed:
            wanted.setdefault(pid, set()).add(vid)
        reads = [discovery]
        for pid, variants in wanted.items():
            if pid not in available:
                discovery.errors.append(self._error('requested product is not in bounded discovery'))
                continue
            read = self._read(self._scoped(fetch), available[pid], query, only=frozenset(variants))
            if read.listed and variants.difference(read.variation_ids):
                read.errors.append(self._error('requested variation is no longer listed'))
            reads.append(read)
        return self._result(reads)



ADAPTER = ClassCatalogAdapter(CONFIG)
