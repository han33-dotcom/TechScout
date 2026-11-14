"""Beytech reader (MVP 05): the iPhone 17 '1 Sim - 2 Years Warranty' product and two policy pages.

The product name carries the SIM and warranty statements, and a non-variation Brand attribute
names Apple (the page's JSON-LD brand reads 'Electronics' and is not used). The warranty page
covers repairs only, with the original invoice; the exchange page allows one exchange within
5 days but lists mobiles as non-returnable. Neither page settles an offer field.
"""
import re
from types import MappingProxyType

from app.adapters.base import PolicyFact
from app.adapters.woocommerce import (
    Claim, PageFacts, PolicyPage, ProductEndpoints, StoreConfig, WooCommerceAdapter, WarrantyStatement,
)

SOURCE_ID = 'beytech'
ORIGIN = 'https://beytech.com.lb'

_PAYMENT_ICONS = re.compile(r'Cash\s+On\s+Delivery\s+(?:Visa|MasterCard|American\s+Express)\b', re.IGNORECASE)
_NAME_WARRANTY = re.compile(r'(?<![0-9a-z])([0-9]{1,2})\s*Years?\s+Warranty\b', re.IGNORECASE)
_COVERS = re.compile(r'warranty\s+covers\s+repairs\s+only\b[^.]{0,80}\.?', re.IGNORECASE)
_INVOICE = re.compile(r'Warranty\s+service\s+will\s+only\s+be\s+provided\s+upon\s+presentation\s+of\s+the\s+'
                      r'original\s+sales\s+invoice\b[^.]{0,80}\.?', re.IGNORECASE)
_EXCHANGE = re.compile(r'exchanged\s+once\s+within\s+([0-9]{1,2})\s+days\b[^.]{0,80}\.?', re.IGNORECASE)
_NO_MOBILES = re.compile(r'Non-Returnable\s+Items\s*:[^:]{0,120}?\bMobiles\b', re.IGNORECASE)


def page_facts(text: str) -> PageFacts:
    """The footer's payment icons: cash on delivery and cards."""
    icons = _PAYMENT_ICONS.search(text)
    return PageFacts(payment_methods=None if icons is None else Claim(('cash', 'card'), icons.group(0),
                                                                      scope='source'))


def name_warranty(name: str) -> WarrantyStatement | None:
    """'2 Years Warranty' in the product name: 24 months, provider not named."""
    match = _NAME_WARRANTY.search(name)
    return None if match is None else (None, int(match.group(1)) * 12)


def warranty_facts(text: str) -> tuple[PolicyFact, ...]:
    """What the warranty covers and that it needs the invoice; no length, provider or territory."""
    covers, invoice = _COVERS.search(text), _INVOICE.search(text)
    value = {**({'covers': 'repairs only'} if covers else {}),
             **({'invoice_required': True} if invoice else {})}
    if not value:
        return ()
    return (PolicyFact('warranty', value, ' '.join(m.group(0) for m in (covers, invoice) if m)),)


def exchange_facts(text: str) -> tuple[PolicyFact, ...]:
    """The exchange window, and whether mobiles are excluded from it."""
    exchange = _EXCHANGE.search(text)
    if exchange is None:
        return ()
    excluded = _NO_MOBILES.search(text)
    value = {'days': int(exchange.group(1)), **({'excludes_mobiles': True} if excluded else {})}
    return (PolicyFact('returns', value, ' '.join(m.group(0) for m in (exchange, excluded) if m)),)


CONFIG = StoreConfig(
    source_id=SOURCE_ID,
    adapter_version='beytech/1',
    products=(ProductEndpoints(
        key='139968', label='iPhone 17',
        api_url=f'{ORIGIN}/wp-json/wc/store/v1/products?slug=apple-iphone-17-1-sim-2-years-warranty',
        page_url=f'{ORIGIN}/product/apple-iphone-17-1-sim-2-years-warranty/'),),
    policies=(
        PolicyPage('warranty', f'{ORIGIN}/warranty-terms-conditions/',
                   ('warranty.provider', 'warranty.months', 'warranty.territory'), warranty_facts),
        PolicyPage('exchange', f'{ORIGIN}/exchange-policy-2/', (), exchange_facts),
    ),
    # One physical SIM slot plus eSIM, as Apple sells the iPhone 17 in this region.
    name_sim=MappingProxyType({'1 sim': 'physical+esim'}),
    page_facts=page_facts,
    name_warranty=name_warranty,
)
ADAPTER = WooCommerceAdapter(CONFIG)
