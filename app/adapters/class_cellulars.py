"""Class Cellulars reader (MVP 05): the iPhone 17 product and the FAQ page.

The product page states VAT inclusion next to the price, a 14-30 day delivery estimate, an
18-month warranty at Genuine Warranty Center and a free-shipping banner. The FAQ states delivery
times for Beirut and outside it, cash-on-delivery-only payment and a three-day return window. The
delivery time can conflict with the product page's estimate; apply_policy keeps both. The FAQ
speaks to online (delivery) orders, so it settles nothing for a pickup offer.
"""
from decimal import Decimal
import re
from types import MappingProxyType

from app.adapters.base import PolicyDocument, PolicyFact
from app.adapters.woocommerce import (
    ARRIVAL_CONFLICT, MAX_POLICY_DAYS, PAYMENT_CONFLICT, Claim, PageFacts, PolicyPage, ProductEndpoints,
    StoreConfig, WooCommerceAdapter, decimal_amount, merge_policy_fact, policy_window,
)
from app.contracts import Offer

SOURCE_ID = 'class_cellulars'
ORIGIN = 'https://classcellulars.com'
MAX_VAT_CLAIMS = 50

# Prices read '1,030.00 $ (Inc. VAT)'; the left boundary stops a match inside a longer number.
_PRICE_VAT = re.compile(r'(?<![0-9.,])([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(\.[0-9]{1,6})?\s*\$\s*'
                        r'\(\s*Inc\.\s*VAT\s*\)', re.IGNORECASE)
_ESTIMATE = re.compile(r'Estimated\s+delivery\s+time\s+([0-9]{1,3})\s*[-\u2013]\s*([0-9]{1,3})\s+days\b',
                       re.IGNORECASE)
_WARRANTY = re.compile(r'(?<![0-9])([0-9]{1,3})\s+months?\s+warranty\s+at\s+'
                       r'Genuine\s+Warranty\s+Cent(?:er|re)\b', re.IGNORECASE)
_BANNER = re.compile(r'Free\s+Shipping\s+On\s+All\s+Orders\s+Over\s+\$\s*'
                     r'([0-9]{1,6}(?:\.[0-9]{1,2})?)(?![0-9])', re.IGNORECASE)
_BEIRUT = re.compile(r'Within\s+Beirut\s*:\s*delivery\s+takes\s+([0-9]{1,2})\s+working\s+days?\b',
                     re.IGNORECASE)
_OUTSIDE = re.compile(r'Outside\s+Beirut\s*:\s*delivery\s+takes\s+up\s+to\s+([0-9]{1,2})\s+working\s+days?\b',
                      re.IGNORECASE)
_CASH_ONLY = re.compile(r'we\s+only\s+accept\s+cash\s+on\s+delivery\b', re.IGNORECASE)
_RETURNS = re.compile(r'request\s+a\s+return\s+within\s+([0-9]{1,2})\s+days?\b', re.IGNORECASE)


def page_facts(text: str) -> PageFacts:
    """The product page's own statements; the free-shipping banner is site-wide."""
    vat = tuple(Claim(decimal_amount(m.group(1) + (m.group(2) or '')), m.group(0))
                for m in _PRICE_VAT.finditer(text))[:MAX_VAT_CLAIMS]
    estimate, warranty, banner = _ESTIMATE.search(text), _WARRANTY.search(text), _BANNER.search(text)
    arrival = None
    if estimate is not None and int(estimate.group(1)) <= int(estimate.group(2)):
        arrival = Claim((int(estimate.group(1)), int(estimate.group(2)), 'calendar'), estimate.group(0))
    return PageFacts(
        tax_included=vat, arrival=arrival,
        warranty=None if warranty is None else Claim(
            ('Genuine Warranty Center', int(warranty.group(1))), warranty.group(0)),
        delivery_fee=None if banner is None else Claim(
            {'amount': Decimal('0'), 'currency': 'USD',
             'conditions': (f'min_subtotal:USD:{decimal_amount(banner.group(1))}',)},
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
    policies=(PolicyPage('faq', f'{ORIGIN}/faq/', ('payment_basis', 'arrival'), faq_facts),),
    sim_map=MappingProxyType({'nano sim / esim': 'physical+esim', 'esim': 'esim-only'}),
    page_facts=page_facts,
    policy_effect=apply_faq,
    # The FAQ's store list, observed 2026-11-14.
    branches=(('mar-elias', 'Beirut'), ('abc-verdun', 'Beirut'), ('abc-achrafieh', 'Beirut'),
              ('abc-dbayeh', 'Dbayeh'), ('tyre', 'Tyre')),
)
ADAPTER = WooCommerceAdapter(CONFIG)
