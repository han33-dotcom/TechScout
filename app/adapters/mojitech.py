"""Mojitech reader (MVP 05): the iPhone 17 256GB product and the refund and returns page.

Every page footer reads 'Delivery all over Lebanon', 'Same Day Up To 72 hours (Working Days)'
and 'Cash on delivery or Credit cards'. The ESIM option holds the SIM type, except 'ABM 2YEARS
WARRANTY', which states a two-year ABM warranty and no SIM type.
"""
import math
import re
from types import MappingProxyType

from app.adapters.base import PolicyDocument, PolicyFact
from app.adapters.woocommerce import (
    ARRIVAL_CONFLICT, MAX_POLICY_DAYS, Claim, PageFacts, PolicyPage, ProductEndpoints, StoreConfig,
    WooCommerceAdapter, WarrantyStatement, merge_policy_fact, policy_window,
)
from app.contracts import Offer

SOURCE_ID = 'mojitech'
ORIGIN = 'https://mojitech.net'

_FOOTER_DELIVERY = re.compile(r'Delivery\s+all\s+over\s+Lebanon\s+Same\s+Day\s+Up\s+To\s+'
                              r'([0-9]{1,4})\s+hours\s*\(\s*Working\s+Days\s*\)', re.IGNORECASE)
_FOOTER_PAYMENT = re.compile(r'Payment\s+Methods\s+Cash\s+on\s+delivery\s+or\s+Credit\s+cards\b',
                             re.IGNORECASE)
_RETURNS = re.compile(r'refund\s+and\s+returns\s+policy\s+lasts\s+([0-9]{1,3})\s+days\b', re.IGNORECASE)
_ABM = re.compile(r'\s*ABM\s*([0-9]{1,2})\s*YEARS?\s+WARRANTY\s*', re.IGNORECASE)


def _delivery(text: str) -> tuple[str, int] | None:
    """The footer's claim and its upper bound in working days; a part day counts as a whole one."""
    match = _FOOTER_DELIVERY.search(text)
    if match is None:
        return None
    days = math.ceil(int(match.group(1)) / 24)
    return (match.group(0), days) if days <= MAX_POLICY_DAYS else None


def page_facts(text: str) -> PageFacts:
    """Only the site-wide footer speaks to delivery and payment."""
    footer, payment = _delivery(text), _FOOTER_PAYMENT.search(text)
    return PageFacts(
        arrival=None if footer is None else Claim((0, footer[1], 'working'), footer[0], scope='source'),
        payment_methods=None if payment is None else Claim(('cash', 'card'), payment.group(0),
                                                           scope='source'))


def refund_facts(text: str) -> tuple[PolicyFact, ...]:
    """The footer's delivery time and payment methods, and the return window."""
    facts = []
    if footer := _delivery(text):
        facts.append(PolicyFact('delivery_time', {'zone': 'Lebanon', 'min_days': 0, 'max_days': footer[1],
                                                  'unit': 'working'}, footer[0]))
    if match := _FOOTER_PAYMENT.search(text):
        facts.append(PolicyFact('payment', {'methods': ['cash', 'card'], 'only': False}, match.group(0)))
    if match := _RETURNS.search(text):
        facts.append(PolicyFact('returns', {'days': int(match.group(1))}, match.group(0)))
    return tuple(facts)


def option_warranty(value: str) -> WarrantyStatement | None:
    """'ABM 2YEARS WARRANTY' is a 24-month ABM warranty; the other options state none."""
    match = _ABM.fullmatch(value)
    return None if match is None else ('ABM', int(match.group(1)) * 12)


def apply_refund_returns(offer: Offer, policy: PolicyDocument) -> Offer:
    """The footer's delivery time fills an unknown arrival; cash and card leave payment basis unknown."""
    if policy.policy_id != 'refund_returns' or offer.fulfillment.mode != 'delivery':
        return offer
    for fact in policy.facts:
        if fact.topic == 'delivery_time' and fact.value.get('zone') == 'Lebanon':
            window = policy_window(policy.observed_at, fact.value)
            if window is not None:
                offer = merge_policy_fact(offer, policy, fact, 'arrival', window,
                                          conflict_reason=ARRIVAL_CONFLICT, only_if_unknown=True)
    return offer


CONFIG = StoreConfig(
    source_id=SOURCE_ID,
    adapter_version='mojitech/1',
    products=(ProductEndpoints(
        key='91706', api_url=f'{ORIGIN}/wp-json/wc/store/v1/products?slug=apple-iphone-17-256gb',
        page_url=f'{ORIGIN}/shop/brands/apple-brands/apple-iphone-17-256gb/', label='iPhone 17'),),
    policies=(PolicyPage('refund_returns', f'{ORIGIN}/refund_returns/', ('arrival',), refund_facts),),
    warranty_names=frozenset({'esim'}),
    sim_map=MappingProxyType({'esim act': 'esim-only', 'esim act tech+': 'esim-only',
                              'abm 2years warranty': None}),
    page_facts=page_facts,
    option_warranty=option_warranty,
    policy_effect=apply_refund_returns,
)
ADAPTER = WooCommerceAdapter(CONFIG)
