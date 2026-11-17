"""Ayoub Computers marketplace reader (T19): bounded BigCommerce search and product pages.

ayoubcomputers.com is a multi-vendor marketplace: a listing may be sold and fulfilled by Ayoub
Computers or by a third-party vendor. Each listing is a separate BigCommerce product, so two
listings of one model stay two candidates with their own seller, never one merged seller.

Facts come only from the listing's own structured data: the BCData and JSON-LD blocks (price,
online stock, purchasability, condition), the details list and the custom-field table
(configuration, condition, warranty, seller and fulfiller fields). The marketplace notice and
the VAT footer are store-wide template text: they are kept as source-scoped policy statements
and never become a listing's seller or fulfiller. The VAT is marked as an unknown charge, never
zero and never computed here. Titles are not parsed for configuration. Online stock is not
branch stock, and no branch facts are produced. Excerpts are verbatim quotations of the page.

Candidates are corpus observations, not verified offers; current-run verification, identity
and eligibility still decide recommendations. See docs/ayoub-catalog.md.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from hashlib import sha256
from html.parser import HTMLParser
import itertools
import json
import re
from urllib.parse import quote, urlsplit

from app.adapters.base import Snapshot, json_pointer, tool_result
from app.adapters.woo_catalog import CatalogCandidate, CatalogResult
from app.contracts import Fact, ToolError, ToolResult, Warranty
from app.contracts_v2 import Charge, Evidence
from app.corpus_contracts import Extraction, FieldObservation
from app.source_capabilities import CapabilityRef, safe_endpoint

SOURCE_ID = 'ayoub_computers'
ORIGIN = 'https://ayoubcomputers.com'
HOST = 'ayoubcomputers.com'
ADAPTER_VERSION = 'ayoub-catalog/1'
POLICY_VERSION = 'ayoub-policy/1'
OPERATOR_ID = 'ayoub_computers'
TERMS_URL = f'{ORIGIN}/terms-of-service/'
DELIVERY_URL = f'{ORIGIN}/quick-delivery-guide/'
CURRENCIES = frozenset({'USD', 'LBP', 'EUR', 'AED'})

# Fixed reasons: store text never becomes a reason.
PRICE_ABSENT = 'price is not published'
PRICE_INVALID = 'price is zero, malformed or in an unsupported currency'
CALL_FOR_PRICE = 'store asks buyers to call for the price'
NOT_PURCHASABLE = 'listing is not explicitly purchasable'
OPTION_PRICED = 'option-specific price and stock load only after an option is selected'
BACKORDER = 'backorder is not current stock'
AVAILABILITY_UNCLEAR = 'availability is not stated as in or out of stock'
CONDITION_UNCLEAR = 'condition is not stated as new, used or refurbished'
NOT_PUBLISHED = 'not published'
SELLER_UNNAMED = ('listing does not name its seller; the store sells its own and '
                  'third-party vendor inventory')
FULFILLER_UNNAMED = ('listing does not name its fulfiller; the store-wide fulfillment notice '
                     'is not listing-specific')
PARTY_INVALID = 'listing names a seller or fulfiller that is not a bounded plain name'
WARRANTY_ABSENT = 'warranty is not published'
WARRANTY_UNCLEAR = 'warranty duration is not stated as a plain period'
WARRANTY_PROVIDER = 'listing does not name who services the warranty'
WARRANTY_TERRITORY = 'warranty territory is not published'
SPEC_UNCLEAR = 'specification is not stated in a recognized form'
TAX_EXCLUDED = 'listed price excludes VAT; the store adds it at checkout'
TAX_UNPUBLISHED = 'tax treatment is not published on the listing page'
CONFLICT = 'source values disagree for this listing'

_SEARCH_TEXT = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9._-]*( [a-zA-Z0-9][a-zA-Z0-9._-]*)*')
_PRODUCT_ID = re.compile(r'[1-9][0-9]{0,9}')
_SLUG_PATH = re.compile(r'/[a-z0-9][a-z0-9-]{0,199}/')
_ATTRIBUTE = re.compile(r'attribute\[([1-9][0-9]{0,9})\]')
# A capacity, optionally followed by one plain memory or drive word ('256GB SSD', '16GB Unified Memory').
_GB = re.compile(r'([1-9][0-9]{0,4})\s*(GB|TB)(?:\s+(?:SSD|NVMe SSD|NVMe|RAM|Unified Memory|LPDDR[45]X?|'
                 r'DDR[45]))?\.?', re.IGNORECASE)
_DESCRIPTION_LINE = re.compile(r'(RAM|Memory|Storage)\s*:\s*(.+)', re.IGNORECASE)
_INCHES = re.compile(r'([1-9][0-9]?(?:\.[0-9])?)\s*(?:"|”|\'\'|inch|inches)?', re.IGNORECASE)
# An optional short parenthetical label may follow ('3840 x 2160 (4K UHD)'); it is not read.
_LABEL = r'(?:\s*\([A-Za-z0-9 +.-]{1,20}\))?'
_RESOLUTION = re.compile(r'([1-9][0-9]{2,4})\s*[xX×]\s*([1-9][0-9]{2,4})(?:\s*(?:pixels|px))?' + _LABEL,
                         re.IGNORECASE)
_HERTZ = re.compile(r'([1-9][0-9]{0,3})\s*Hz' + _LABEL, re.IGNORECASE)
_PERIOD = re.compile(r'([1-9][0-9]?)\s*(years?|yrs?|months?)', re.IGNORECASE)
_PARTY_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .&'-]{0,79}")
_OPTION_VALUE = re.compile(r'[1-9][0-9]{0,9}')
_BCDATA = re.compile(r'\s*var\s+BCData\s*=\s*(\{.*\})\s*;?\s*', re.DOTALL)
_VAT_FOOTER = re.compile(r'Prices\s+exclude\s+[0-9]{1,2}%\s+VAT,\s+applied\s+at\s+checkout', re.IGNORECASE)
_MARKETPLACE_NOTICE = (
    re.compile(r'Fulfilled\s+by\s+AyoubComputers\.com', re.IGNORECASE),
    re.compile(r'Certain\s+products\s+listed\s+on\s+this\s+page\s+may\s+be\s+sold,\s+invoiced,\s+and\s+'
               r'fulfilled\s+directly\s+by\s+independent\s+third-party\s+vendors\s+registered\s+with\s+us\.',
               re.IGNORECASE),
)
_OPERATOR_NAMES = frozenset({'ayoubcomputers', 'ayoubcomputerscom'})
_SCHEMA_CONDITION = {'https://schema.org/newcondition': 'new', 'https://schema.org/usedcondition': 'used',
                     'https://schema.org/refurbishedcondition': 'refurbished'}
_SCHEMA_STOCK = {'https://schema.org/instock': True, 'https://schema.org/outofstock': False,
                 'https://schema.org/soldout': False}
# Discovery order only: a card whose title names an accessory the buyer did not search for is read
# last. No fact comes from a card title; every page still has to show the category's own specs.
_ACCESSORY_WORDS = frozenset({'adapter', 'arm', 'backpack', 'bag', 'bracket', 'bumper', 'cable', 'case',
                              'charger', 'cover', 'holder', 'mount', 'protector', 'replacement', 'shell',
                              'skin', 'sleeve', 'stand'})
_SUPPORTED_OPTIONS = frozenset({'swatch', 'set-radio', 'set-rectangle', 'set-select'})
_FIELD_ROLES = {
    'cpu': 'cpu', 'processor': 'cpu', 'ram': 'ram', 'memory': 'ram', 'ssd': 'storage',
    'storage': 'storage', 'gpu': 'gpu', 'graphics': 'gpu', 'size': 'size', 'screensize': 'size',
    'displaysize': 'size', 'resolution': 'resolution', 'refreshrate': 'refresh',
    'operatingsystem': 'os', 'os': 'os', 'condition': 'condition', 'warranty': 'warranty',
    'availability': 'availability', 'brand': 'brand', 'manufacturer': 'brand', 'color': 'color',
    'colour': 'color', 'soldby': 'seller', 'seller': 'seller', 'vendor': 'seller',
    'fulfilledby': 'fulfiller', 'shippedby': 'fulfiller', 'fulfiller': 'fulfiller',
}
# Category-specific spec fields; a page lacking a category's mandatory specs is not read as one.
_SPEC_FIELDS = {
    'laptop': {'cpu': 'variant.specs.cpu', 'ram': 'variant.specs.ram_gb', 'storage': 'variant.specs.storage_gb',
               'gpu': 'variant.specs.gpu', 'os': 'variant.specs.operating_system',
               'size': 'variant.specs.display_inches', 'width': 'variant.specs.display_width_px',
               'height': 'variant.specs.display_height_px', 'refresh': 'variant.specs.display_refresh_hz'},
    'monitor': {'size': 'variant.specs.diagonal_inches', 'width': 'variant.specs.width_px',
                'height': 'variant.specs.height_px', 'refresh': 'variant.specs.refresh_hz'},
}
_REQUIRED_SPECS = {
    'laptop': ('variant.specs.cpu', 'variant.specs.ram_gb', 'variant.specs.storage_gb'),
    'monitor': ('variant.specs.width_px', 'variant.specs.height_px', 'variant.specs.refresh_hz',
                'variant.specs.diagonal_inches'),
}
_HIDDEN = frozenset({'script', 'style', 'noscript', 'template', 'svg'})
# Block boundaries separate words in extracted text ('Zone A' and '2 - 5' in two elements).
_BLOCK = frozenset({'address', 'article', 'aside', 'blockquote', 'br', 'dd', 'div', 'dl', 'dt', 'fieldset',
                    'figcaption', 'figure', 'footer', 'form', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'header',
                    'hr', 'li', 'main', 'nav', 'ol', 'p', 'section', 'table', 'td', 'th', 'tr', 'ul'})
_VOID = frozenset({'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source',
                   'track', 'wbr'})


class _SchemaError(ValueError):
    """The page does not have the shape this reader understands."""


def _key(value: str) -> str:
    return re.sub(r'[^a-z0-9]', '', value.casefold())


def _text(value: str) -> str:
    return ' '.join(value.split())


def _strict_json(text: str):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise _SchemaError('duplicate JSON key')
            result[key] = value
        return result

    def constant(_):
        raise _SchemaError('non-finite number')
    return json.loads(text, parse_float=Decimal, object_pairs_hook=pairs, parse_constant=constant)


@dataclass
class _Option:
    attribute: str
    kind: str
    name: str = ''
    values: list = field(default_factory=list)  # (value_id, label) in page order
    supported: bool = True
    name_done: bool = False


class _ProductPage(HTMLParser):
    """The parts of a BigCommerce product page this reader uses, with their content regions.

    Every step is constant time: open-element flags are counted, not searched, so deeply nested
    markup cannot make parsing quadratic. Structure this reader does not expect (a second details
    table, a table nested in one, a cell outside a row) is counted in `malformed` and refused.
    """
    _FLAGS = ('warranty_tab', 'warranty', 'options', 'option')

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.raw = ''
        self.json_ld: list[str] = []
        self.scripts: list[str] = []
        self.canonical: list[str] = []
        self.product_ids: list[str] = []
        self.info: list[tuple[str, str]] = []
        self.fields: list[tuple[str, str]] = []
        self.warranty: list[str] = []
        self.options: list[_Option] = []
        self.unsupported_inputs = 0
        self.malformed = 0
        self.counts = {'table': 0, 'dl': 0, 'warranty_tab': 0}
        self.regions: dict[str, list[str]] = {}
        self.footer: list[str] = []
        self.visible: list[str] = []
        self._script: list[str] | None = None
        self._script_ld = False
        self._hidden = 0
        self._divs: list[tuple[tuple[str, ...], bool]] = []  # flags set, and whether a region opened
        self._active = dict.fromkeys(self._FLAGS, 0)
        self._region_stack: list[str] = []
        self._dl = 0
        self._dt: list[str] | None = None
        self._dd: list[str] | None = None
        self._pending_dt: str | None = None
        self._table = 0
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._label: list[str] | None = None
        self._select: str | None = None
        self._option_value: tuple[str, list[str]] | None = None
        self._footer = 0

    def handle_starttag(self, tag, attrs):
        values = {}
        for name, value in attrs:
            values.setdefault(name, value or '')
        classes = values.get('class', '').split()
        if tag == 'script':
            self._script = []
            self._script_ld = values.get('type', '').strip().lower() == 'application/ld+json'
            return
        if tag in _HIDDEN:
            self._hidden += 1
            return
        if tag in _BLOCK:
            self._break()
        if tag == 'link' and values.get('rel', '').strip().lower() == 'canonical':
            self.canonical.append(values.get('href', ''))
        if tag == 'footer':
            self._footer += 1
        if tag == 'div':
            self._open_div(values, classes)
        elif tag == 'dl':
            if self._dl:
                self._dl += 1
                self.malformed += 1  # a list nested in the details list
            elif 'productView-info' in classes:
                self._dl = 1
                self.counts['dl'] += 1
        elif tag == 'dt' and self._dl == 1:
            self._dt = []
        elif tag == 'dd' and self._dl == 1:
            self._dd = []
        elif tag == 'table':
            if self._table:
                self._table += 1
                self.malformed += 1  # a table nested in the custom-field table
            elif 'productView-addition-table' in classes:
                self._table = 1
                self.counts['table'] += 1
        elif tag == 'tr' and self._table == 1:
            self._end_row()
            self._row = []
        elif tag == 'td' and self._table == 1:
            if self._row is None:
                self.malformed += 1
            else:
                self._end_cell()
                self._cell = []
        elif tag == 'label' and self._active['option'] and self.options and not self.options[-1].name_done:
            self._label = []
        elif tag in {'span', 'small'} and self._label is not None and (
                tag == 'small' or 'data-option-value' in values):
            self._finish_label()
        elif tag == 'input':
            if values.get('name') == 'product_id' and values.get('type', '').lower() == 'hidden':
                self.product_ids.append(values.get('value', ''))
            elif self._active['options']:
                self._option_input(values)
        elif tag == 'select' and self._active['options']:
            match = _ATTRIBUTE.fullmatch(values.get('name', ''))
            if match is None or not self.options or not self._active['option'] or 'required' not in values:
                self.unsupported_inputs += 1  # an optional choice hides a "none" configuration
            else:
                option = self.options[-1]
                option.attribute = option.attribute or match.group(1)
                if option.attribute != match.group(1):
                    option.supported = False
                self._select = match.group(1)
        elif tag == 'option' and self._select is not None:
            self._option_value = (values.get('value', ''), [])
        elif tag == 'textarea' and self._active['options']:
            self.unsupported_inputs += 1

    def _open_div(self, values, classes):
        flags = []
        if values.get('id') == 'tab-warranty':
            flags.append('warranty_tab')
            self.counts['warranty_tab'] += 1
        elif 'productView-description-tabContent' in classes and self._active['warranty_tab']:
            flags.append('warranty')  # the tab's text, without its heading and collapse links
        if 'data-product-option-change' in values:
            flags.append('options')
        if 'form-field' in classes and self._active['options'] and 'data-product-attribute' in values:
            self.options.append(_Option('', values['data-product-attribute']))
            flags.append('option')
        for flag in flags:
            self._active[flag] += 1
        region = 'data-content-region' in values
        if region:
            self._region_stack.append(values['data-content-region'])
        self._divs.append((tuple(flags), region))

    def _close_div(self):
        if not self._divs:
            return
        flags, region = self._divs.pop()
        for flag in flags:
            self._active[flag] -= 1
        if region:
            self._region_stack.pop()

    def _option_input(self, values):
        kind = values.get('type', '').lower()
        match = _ATTRIBUTE.fullmatch(values.get('name', ''))
        if kind != 'radio' or match is None or not self.options or not self._active['option']:
            if kind != 'hidden':
                self.unsupported_inputs += 1
            return
        option = self.options[-1]
        option.attribute = option.attribute or match.group(1)
        if option.attribute != match.group(1) or 'required' not in values:
            option.supported = False
        option.values.append((values.get('value', ''), _text(values.get('data-product-attribute-label', ''))))

    def _finish_label(self):
        option = self.options[-1]
        option.name = _text(''.join(self._label)).rstrip(':').strip()
        option.name_done = True
        self._label = None

    def _end_cell(self):
        if self._cell is not None:
            self._row.append(_text(''.join(self._cell)))
            self._cell = None

    def _end_row(self):
        """A row ends at its end tag, the next row or the table's end; HTML may omit </td> and </tr>."""
        if self._row is None:
            return
        self._end_cell()
        if len(self._row) == 2:
            self.fields.append((self._row[0].rstrip(':').strip(), self._row[1]))
        elif self._row:
            self.malformed += 1  # a row that is not one name and one value is never guessed
        self._row = None

    def handle_endtag(self, tag):
        if tag == 'script':
            if self._script is not None:
                (self.json_ld if self._script_ld else self.scripts).append(''.join(self._script))
            self._script = None
            return
        if tag in _HIDDEN:
            self._hidden = max(0, self._hidden - 1)
            return
        if tag in _BLOCK:
            self._break()
        if tag == 'footer':
            self._footer = max(0, self._footer - 1)
        if tag == 'div':
            self._close_div()
        elif tag == 'dt' and self._dt is not None:
            self._pending_dt = _text(''.join(self._dt))
            self._dt = None
        elif tag == 'dd' and self._dd is not None:
            if self._pending_dt is not None:
                self.info.append((self._pending_dt.rstrip(':').strip(), _text(''.join(self._dd))))
            self._pending_dt, self._dd = None, None
        elif tag == 'dl' and self._dl:
            self._dl -= 1
        elif tag == 'td' and self._table == 1:
            if self._row is not None:
                self._end_cell()
        elif tag == 'tr' and self._table == 1:
            self._end_row()
        elif tag == 'table' and self._table:
            if self._table == 1:
                self._end_row()
            self._table -= 1
        elif tag == 'label' and self._label is not None:
            self._finish_label()
        elif tag == 'option' and self._option_value is not None:
            value, label = self._option_value
            if value:
                self.options[-1].values.append((value, _text(''.join(label))))
            self._option_value = None
        elif tag == 'select':
            self._select = None

    def close(self):
        super().close()
        if self._table or self._dl:
            self.malformed += 1  # the document ended inside the details list or table

    def _break(self):
        if self._script is None and not self._hidden:
            self._collect(' ')

    def handle_data(self, data):
        if self._script is not None:
            self._script.append(data)
        elif not self._hidden:
            self._collect(data)

    def _collect(self, data):
        """Visible text into every collector open at this point."""
        self.visible.append(data)
        for buffer in (self._dt, self._dd, self._cell, self._label):
            if buffer is not None:
                buffer.append(data)
        if self._option_value is not None:
            self._option_value[1].append(data)
        if self._active['warranty']:
            self.warranty.append(data)
        if self._region_stack:
            self.regions.setdefault(self._region_stack[-1], []).append(data)
        if self._footer:
            self.footer.append(data)


def _read_page(body: bytes) -> _ProductPage:
    page = _ProductPage()
    page.raw = body.decode('utf-8', errors='replace')
    page.feed(page.raw)
    page.close()
    return page


class _SearchPage(HTMLParser):
    """Product cards on a BigCommerce search page: product ID, title link and order."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cards: list[dict] = []
        self._card: dict | None = None
        self._depth = 0
        self._title: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        values = {}
        for name, value in attrs:
            values.setdefault(name, value or '')
        classes = values.get('class', '').split()
        if tag == 'article' and 'card' in classes:
            self._card, self._depth = {'ids': set(), 'hrefs': set(), 'title': ''}, 1
            self.cards.append(self._card)
            return
        if self._card is None:
            return
        if tag == 'article':
            self._depth += 1
        for name in ('data-product-id', 'data-compare-id'):
            if name in values:
                self._card['ids'].add(values[name])
        if tag == 'a' and self._title is None and self._card.get('in_title'):
            self._card['hrefs'].add(values.get('href', ''))
            self._title = []
        if tag == 'h4' and 'card-title' in classes:
            self._card['in_title'] = True

    def handle_endtag(self, tag):
        if self._card is None:
            return
        if tag == 'a' and self._title is not None:
            self._card['title'] = _text(''.join(self._title))
            self._title = None
        elif tag == 'h4':
            self._card['in_title'] = False
        elif tag == 'article':
            self._depth -= 1
            if not self._depth:
                self._card = None

    def handle_data(self, data):
        if self._title is not None:
            self._title.append(data)


@dataclass(frozen=True)
class PartyObservation:
    """A listing's seller or fulfiller, with the receipts it cites.

    party_id is set only for a known name: the store operator under its own ID, and every other
    vendor under its own marketplace ID, so distinct vendors never share a seller.
    """
    fact: Fact[str]
    party_id: str | None
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class WarrantyObservation:
    """The listing's own warranty statement; responsibility terms are source policy text."""
    warranty: Warranty
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class MarketplaceCandidate(CatalogCandidate):
    """A catalog candidate plus what a marketplace listing must keep apart.

    `notices` holds the page's store-wide, source-scoped statements (VAT footer, marketplace
    notice) separately from the listing's own `extraction`. `costs` marks the VAT as unknown,
    never zero. `identifiers` (SKU, MPN) are kept only for listings without options, with their
    receipts in `evidence` alongside the cost receipts. A candidate whose seller is unknown cannot
    become a v2 Offer: it stays provisional, and the seller must never default to the operator.
    """
    seller: PartyObservation | None = None
    fulfiller: PartyObservation | None = None
    warranty: WarrantyObservation | None = None
    identifiers: tuple[tuple[str, str], ...] = ()
    notices: Extraction | None = None
    costs: tuple[Charge, ...] = ()
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class PolicyResult:
    extraction: Extraction
    tool_result: ToolResult
    snapshots: tuple[Snapshot, ...]
    missing_topics: tuple[str, ...]


def party_id(name: str) -> str:
    """The operator's own ID, or an ID unique to one party's exact name.

    Ayoub's terms name Ayoub Computers as the operator of ayoubcomputers.com, so both names are
    the operator. Any other name keeps a readable slug plus a digest of its spelling (case and
    spacing aside), so names differing only in punctuation are never merged into one party.
    """
    if _key(name) in _OPERATOR_NAMES:
        return OPERATOR_ID
    spelling = _text(name).casefold()
    slug = '-'.join(re.findall(r'[a-z0-9]+', spelling))
    return f'{OPERATOR_ID}.party.{slug}-{sha256(spelling.encode()).hexdigest()[:8]}'


def _same(a, b):
    if isinstance(a, str) and isinstance(b, str):
        return a.casefold() == b.casefold()
    return a == b


def _compose(entries, *, force_reason=None):
    """Readings of one field to a fact dict: unknown, conflict or known, citing every receipt."""
    receipts = tuple({r.evidence_id: r for _, r, _ in entries}.values())
    fact = {'evidence_ids': [r.evidence_id for r in receipts]}
    values = []
    for value, _, reason in entries:
        if reason is None and value is not None and not any(_same(value, v) for v in values):
            values.append(value)
    reasons = sorted({reason for _, _, reason in entries if reason})
    if force_reason:
        fact.update(state='unknown', reason=force_reason)
    elif reasons or not values:
        fact.update(state='unknown', reason='; '.join(reasons) or NOT_PUBLISHED)
    elif len(values) > 1:
        fact.update(state='conflict', alternatives=values, reason=CONFLICT)
    else:
        fact.update(state='known', value=values[0])
    return fact, receipts


def _gigabytes(match):
    return int(match.group(1)) * (1024 if match.group(2).upper() == 'TB' else 1)


def _months(text):
    match = _PERIOD.fullmatch(_text(text))
    if match is None:
        return None
    unit = match.group(2).casefold()
    return int(match.group(1)) * (1 if unit.startswith('month') else 12)


class AyoubCatalog:
    """Bounded Ayoub discovery: one search page and at most two product pages per call."""
    max_products = 2
    max_cards = 60
    max_variants = 12
    max_fields = 80
    categories = ('laptop', 'monitor')

    def __init__(self):
        self.source_id, self.origin, self.adapter_version = SOURCE_ID, ORIGIN, ADAPTER_VERSION

    def _error(self, reason):
        return ToolError(code='INVALID_SCHEMA', message=f'{SOURCE_ID}: {reason}', retryable=False)

    @staticmethod
    def _cap(kind):
        return CapabilityRef(capability_id=f'{SOURCE_ID}-{kind}', version='1')

    @staticmethod
    def product_url(product_id: str) -> str:
        return f'{ORIGIN}/products.php?productId={product_id}'

    def search(self, fetch, *, category, search, run=None, only=None, corpus_version='catalog-preview-v1'):
        """Read one search page and at most two exact product pages (three gate calls).

        With `only`, the named (product_id, variant_id) pairs are re-read directly from their
        product pages, without discovery; nothing cached substitutes for a fresh read.
        """
        if category not in self.categories or not isinstance(search, str) or len(search) > 64 \
                or not _SEARCH_TEXT.fullmatch(search):
            raise ValueError('A supported category and bounded search string are required')
        wanted = None
        if only is not None:
            wanted = {(str(p), str(v)) for p, v in only}
            if not wanted or any(not _PRODUCT_ID.fullmatch(p) for p, _ in wanted):
                raise ValueError('Re-reads name existing Ayoub product IDs')
        outcomes, errors, snapshots, candidates = [], [], [], []
        stats = {'cards_seen': 0, 'invalid_cards': 0, 'skipped_not_category': 0, 'discovery_truncated': False}

        def get(url, kind):
            outcome = fetch(SOURCE_ID, url, expect='html', capability=self._cap(kind))
            outcomes.append(outcome)
            if not outcome.errors and outcome.body is not None:
                snapshots.append(Snapshot.of(outcome))
                return outcome
            return None

        if wanted is not None:
            targets = [(pid, None) for pid in sorted({p for p, _ in wanted})]
            if len(targets) > self.max_products:
                targets = targets[:self.max_products]
                errors.append(self._error('re-read truncated by configured bound'))
        else:
            targets = []
            found = get(f'{ORIGIN}/search.php?search_query={quote(search)}&section=product', 'search')
            if found:
                try:
                    targets = self._discover(found, search, stats)
                except (_SchemaError, ValueError, TypeError, AssertionError):
                    errors.append(self._error('invalid or conflicting search results'))
                if stats['discovery_truncated']:
                    errors.append(self._error('catalog discovery truncated by configured bound'))
                if stats['invalid_cards']:
                    errors.append(self._error('search cards without a safe, unique listing identity were skipped'))
        read_pages = set()
        for pid, card_url in targets:
            page = get(self.product_url(pid), 'product')
            if page is None:
                continue
            try:
                read = self._extract(page, pid, card_url, category, run, wanted, corpus_version)
            except (_SchemaError, ValueError, TypeError, KeyError, AttributeError, IndexError,
                    ArithmeticError, RecursionError, AssertionError):
                errors.append(self._error('unsupported or conflicting product data'))
                continue
            read_pages.add(pid)
            if read is None:
                stats['skipped_not_category'] += 1
                continue
            candidates.extend(read)
        if stats['skipped_not_category']:
            # An accessory, or a product whose specifications are written in another form.
            errors.append(self._error('pages without the structured specifications of the category were skipped'))
        if wanted is not None:
            found = {(c.product_id, c.variant_id) for c in candidates}
            if any(p in read_pages and (p, v) not in found for p, v in wanted):
                errors.append(self._error('a requested configuration is not on its current product page'))
        data = {'candidates_seen': len(candidates), 'priced_candidates': sum(c.priced for c in candidates),
                'sellers_named': sum(c.seller.party_id is not None for c in candidates),
                'coverage_complete': False, 'requires_current_run_verification': True, **stats}
        ids = [e.evidence_id for c in candidates for e in _receipts(c)]
        return CatalogResult(tuple(candidates), tool_result(outcomes, errors, data, ids, bool(candidates)),
                             tuple(snapshots))

    def _discover(self, outcome, search, stats):
        """Distinct product IDs in page order, ranked for the bounded read.

        Cards whose titles hold every search word come first, and among them those naming no
        accessory the search did not ask for. Invalid cards are skipped and reported.
        """
        page = _SearchPage()
        page.feed(outcome.body.decode('utf-8', errors='replace'))
        page.close()
        if len(page.cards) > self.max_cards:
            raise _SchemaError('too many cards')
        stats['cards_seen'] = len(page.cards)
        seen, ordered = {}, []
        for card in page.cards:
            ids, hrefs = card['ids'], card['hrefs']
            pid, href = next(iter(ids), ''), next(iter(hrefs), '')
            if len(ids) != 1 or len(hrefs) != 1 or not _PRODUCT_ID.fullmatch(pid) or not self._listing_url(href):
                stats['invalid_cards'] += 1
                continue
            if pid in seen:
                if seen[pid] != href:
                    raise _SchemaError('one product ID has two listings')
                continue
            seen[pid] = href
            ordered.append((pid, href, card['title']))
        words = {w.casefold() for w in search.split()}

        def rank(card):
            title = set(re.findall(r'[a-z0-9]+(?:\.[0-9]+)?', card[2].casefold()))
            return (not words <= title, bool((title & _ACCESSORY_WORDS) - words))
        ranked = sorted(ordered, key=rank)  # stable: page order within each rank
        stats['discovery_truncated'] = len(ranked) > self.max_products
        return [(pid, href) for pid, href, _ in ranked[:self.max_products]]

    @staticmethod
    def _listing_url(url):
        if not isinstance(url, str) or not safe_endpoint(url, HOST):
            return False
        parts = urlsplit(url)
        return not parts.query and _SLUG_PATH.fullmatch(parts.path) is not None

    def _extract(self, outcome, pid, card_url, category, run, wanted, corpus_version):
        page = _read_page(outcome.body)
        if set(page.product_ids) != {pid}:
            raise _SchemaError('product page does not show the requested product')
        canonical = set(page.canonical)
        if len(canonical) > 1 or (canonical and not self._listing_url(next(iter(canonical)))):
            raise _SchemaError('invalid canonical listing URL')
        listing_url = next(iter(canonical), None) or self.product_url(pid)
        if card_url is not None and canonical and card_url != listing_url:
            raise _SchemaError('listing changed after discovery')
        products = []
        for i, block in enumerate(page.json_ld):
            data = _strict_json(block)
            if isinstance(data, dict) and data.get('@type') == 'Product':
                products.append((i, data))
        if len(products) > 1:
            raise _SchemaError('more than one product description')
        ld_index, ld = products[0] if products else (None, {})
        bcdata = [m.group(1) for m in (_BCDATA.fullmatch(s) for s in page.scripts) if m]
        if len(bcdata) != 1:
            raise _SchemaError('product data block is missing or repeated')
        attributes = _strict_json(bcdata[0]).get('product_attributes')
        if not isinstance(attributes, dict):
            raise _SchemaError('product attributes are missing')
        if page.malformed or len(page.fields) > self.max_fields or any(
                n > 1 for n in page.counts.values()):
            raise _SchemaError('details are malformed, repeated or unbounded')
        if page.unsupported_inputs or any(not o.supported or o.kind not in _SUPPORTED_OPTIONS
                                          or not o.attribute or not o.values for o in page.options):
            raise _SchemaError('product options are not plain required choices')
        if len({o.attribute for o in page.options}) != len(page.options) or any(
                not _OPTION_VALUE.fullmatch(v) or [w for w, _ in o.values].count(v) != 1
                for o in page.options for v, _ in o.values):
            raise _SchemaError('option IDs are malformed or repeated')
        count = 1
        for option in page.options:  # bounded before any combination is built
            count *= len(option.values)
            if count > self.max_variants:
                raise ValueError('variant bound exceeded')
        combos = list(itertools.product(*[[(o, vid, label) for vid, label in o.values] for o in page.options]))
        name = ld.get('name') if isinstance(ld.get('name'), str) and ld['name'].strip() else None
        candidates = []
        for combo in combos:
            vid = pid if not combo else '-'.join([pid] + [f'{o.attribute}-{v}' for o, v, _ in combo])
            if wanted is not None and (pid, vid) not in wanted:
                continue
            candidate = _Listing(self, outcome, page, pid, vid, category, run, corpus_version).read(
                attributes, ld, ld_index, combo, listing_url, name)
            if candidate is None:
                return None
            candidates.append(candidate)
        return candidates

    def fetch_policies(self, fetch, *, run=None, corpus_version='catalog-preview-v1'):
        """Store-wide marketplace, warranty, tax, delivery, pickup and payment statements.

        Statements are the page's visible text and source-scoped; a receipt quotes the raw page
        only where it holds the statement verbatim, and otherwise points at its offset in the
        visible text. They never resolve an offer's cost, seller,
        branch stock or warranty provider; topics with no recognized statement are named gaps.
        """
        outcomes, snapshots, fields = [], [], {}
        for url, patterns in ((TERMS_URL, _TERMS), (DELIVERY_URL, _DELIVERY)):
            outcome = fetch(SOURCE_ID, url, expect='html')
            outcomes.append(outcome)
            if outcome.errors or outcome.body is None:
                continue
            snapshots.append(Snapshot.of(outcome))
            page = _read_page(outcome.body)
            text = _text(''.join(page.visible))
            for topic, pattern in patterns:
                for match in pattern.finditer(text):
                    statement = _text(match.group(0))
                    field = 'policy.' + topic
                    identity = '/'.join((outcome.response_sha256, outcome.observed_at.isoformat(), field,
                                         statement, str(run), corpus_version))
                    receipt = Evidence(
                        evidence_id=sha256(identity.encode()).hexdigest(), source_id=SOURCE_ID, scope='source',
                        field_path=field, origin_url=outcome.origin_url, final_url=outcome.final_url,
                        observed_at=outcome.observed_at, response_sha256=outcome.response_sha256,
                        adapter_version=POLICY_VERSION, json_pointer=f'/visible-text/{match.start()}',
                        excerpt=_quote(page.raw, statement), origin='runtime', run=run,
                        corpus_version=corpus_version)
                    fields.setdefault(field, []).append((statement, receipt))
        observations = []
        for field, entries in fields.items():
            statements = list(dict.fromkeys(s for s, _ in entries))
            receipts = tuple({r.evidence_id: r for _, r in entries}.values())
            observations.append(FieldObservation(fact={'state': 'known', 'value': ' | '.join(statements),
                                                       'evidence_ids': [r.evidence_id for r in receipts]},
                                                 evidence=receipts))
        missing = tuple(t for t in POLICY_TOPICS if 'policy.' + t not in fields)
        extraction = Extraction(observations=tuple(observations))
        trace = tool_result(outcomes, [], {'missing_topics': list(missing), 'resolved_offer_costs': False},
                            [e.evidence_id for o in observations for e in o.evidence], bool(observations))
        return PolicyResult(extraction, trace, tuple(snapshots), missing)


def _receipts(candidate):
    receipts = [e for o in candidate.extraction.observations for e in o.evidence]
    if candidate.notices is not None:
        receipts.extend(e for o in candidate.notices.observations for e in o.evidence)
    for part in (candidate.seller, candidate.fulfiller, candidate.warranty):
        if part is not None:
            receipts.extend(part.evidence)
    receipts.extend(candidate.evidence)
    return receipts


def _quote(raw, text):
    """The text as an evidence excerpt only when the page holds it verbatim; otherwise None."""
    if text is None:
        return None
    text = _text(text)
    return text if text and len(text) <= 500 and text in raw else None


class _Listing:
    """One configuration of one listing: its readings, then its facts.

    Receipts point at the field they read; an excerpt is attached only when the page holds that
    text verbatim, so an excerpt is always a quotation.
    """

    def __init__(self, catalog, outcome, page, pid, vid, category, run, corpus_version):
        self.catalog, self.outcome, self.page = catalog, outcome, page
        self.pid, self.vid, self.category = pid, vid, category
        self.run, self.corpus_version = run, corpus_version
        self.fields: dict[str, list] = {}
        self.notices: dict[str, list] = {}

    def receipt(self, field, pointer, text=None, *, scope='variant'):
        o, quote = self.outcome, _quote(self.page.raw, text)
        identity = '/'.join((o.response_sha256, o.observed_at.isoformat(), self.pid, self.vid, field, pointer,
                             quote or '', str(self.run), self.corpus_version))
        variant = scope == 'variant'
        return Evidence(
            evidence_id=sha256(identity.encode()).hexdigest(), source_id=SOURCE_ID,
            product_id=self.pid if variant else None, variant_id=self.vid if variant else None, scope=scope,
            field_path=field, origin_url=o.origin_url, final_url=o.final_url, observed_at=o.observed_at,
            response_sha256=o.response_sha256, adapter_version=ADAPTER_VERSION, json_pointer=pointer,
            excerpt=quote, origin='runtime', run=self.run, corpus_version=self.corpus_version)

    def add(self, field, value, pointer, text=None, *, reason=None):
        self.fields.setdefault(field, []).append((value, self.receipt(field, pointer, text), reason))

    def read(self, attributes, ld, ld_index, combo, listing_url, name):
        specs = _SPEC_FIELDS[self.category]
        roles = {'seller': [], 'fulfiller': [], 'warranty': []}
        for index, (label, raw) in enumerate(self.page.fields):
            role = _FIELD_ROLES.get(_key(label))
            pointer = json_pointer('custom-fields', index, label)
            if role in roles:
                roles[role].append((raw, pointer))
            elif role == 'condition':
                value = raw.casefold() if raw.casefold() in {'new', 'used', 'refurbished'} else None
                self.add('condition', value, pointer, raw, reason=None if value else CONDITION_UNCLEAR)
            elif role == 'availability':
                # A merchant's free text; anything but a plain in or out of stock leaves stock unknown.
                value = {'in stock': True, 'out of stock': False}.get(_text(raw).casefold())
                self.add('stock', value, pointer, raw, reason=None if value is not None else AVAILABILITY_UNCLEAR)
            elif role == 'brand' and raw:
                self.add('variant.product.manufacturer', raw, pointer, raw)
            elif role == 'color' and raw:
                self.add('variant.color', raw, pointer, raw)
            else:
                self.spec(role, raw, pointer, specs)
        if self.category == 'laptop' and isinstance(ld.get('description'), str):
            pointer = json_pointer('ld+json', ld_index, 'description')
            for line in ld['description'].splitlines():
                match = _DESCRIPTION_LINE.fullmatch(line.strip())
                gb = match and _GB.fullmatch(match.group(2).strip())
                if gb:
                    role = 'ram' if match.group(1).casefold() in {'ram', 'memory'} else 'storage'
                    self.add(specs[role], _gigabytes(gb), pointer, line)
        brand = ld.get('brand')
        if isinstance(brand, dict) and isinstance(brand.get('name'), str) and brand['name'].strip():
            self.add('variant.product.manufacturer', brand['name'].strip(),
                     json_pointer('ld+json', ld_index, 'brand', 'name'))
        if name:
            self.add('variant.product.model', name, json_pointer('ld+json', ld_index, 'name'))
        for index, (label, raw) in enumerate(self.page.info):
            if _key(label) == 'condition':
                value = raw.casefold() if raw.casefold() in {'new', 'used', 'refurbished'} else None
                self.add('condition', value, json_pointer('details', index, label), raw,
                         reason=None if value else CONDITION_UNCLEAR)
        offer = ld.get('offers') if isinstance(ld.get('offers'), dict) else {}
        condition = offer.get('itemCondition')
        if isinstance(condition, str):
            value = _SCHEMA_CONDITION.get(condition.casefold().replace('http://', 'https://'))
            self.add('condition', value, json_pointer('ld+json', ld_index, 'offers', 'itemCondition'),
                     reason=None if value else CONDITION_UNCLEAR)
        for option, value_id, label in combo:
            pointer = json_pointer('options', f'attribute[{option.attribute}]', value_id)
            if _key(option.name) in {'color', 'colour'} and label:
                self.add('variant.color', label, pointer, label)
            else:
                self.spec(_FIELD_ROLES.get(_key(option.name)), label, pointer, specs)
        if any(not any(r is None for _, _, r in self.fields.get(f, [])) for f in _REQUIRED_SPECS[self.category]):
            return None  # the page does not carry this category's mandatory specifications
        self.offer_state(attributes, offer, ld_index, combo)
        costs, extra = self.store_notices()
        if not combo:  # the embedded identifiers describe the default selection only
            identifiers, receipts = self.identifiers(attributes, ld, ld_index)
            extra += receipts
        else:
            identifiers = ()
        return MarketplaceCandidate(
            SOURCE_ID, self.pid, self.vid, self.category, name or self.pid, listing_url,
            _extraction(self.fields), tuple((o.name, label) for o, _, label in combo),
            seller=self.party('seller', roles['seller'], SELLER_UNNAMED),
            fulfiller=self.party('fulfiller_id', roles['fulfiller'], FULFILLER_UNNAMED),
            warranty=self.warranty(roles['warranty']), identifiers=identifiers,
            notices=_extraction(self.notices), costs=costs, evidence=tuple(extra))

    def spec(self, role, raw, pointer, specs):
        """A configuration reading; text in an unrecognized form is an unknown reading, never dropped."""
        raw = raw.strip()
        if role in {'cpu', 'gpu', 'os'} and role in specs:
            if raw:
                self.add(specs[role], raw, pointer, raw)
        elif role in {'ram', 'storage'} and role in specs:
            m = _GB.fullmatch(raw)
            self.add(specs[role], _gigabytes(m) if m else None, pointer, raw, reason=None if m else SPEC_UNCLEAR)
        elif role == 'size':
            m = _INCHES.fullmatch(raw)
            self.add(specs['size'], m.group(1) if m else None, pointer, raw, reason=None if m else SPEC_UNCLEAR)
        elif role == 'resolution':
            m = _RESOLUTION.fullmatch(raw)
            for key, group in (('width', 1), ('height', 2)):
                self.add(specs[key], int(m.group(group)) if m else None, pointer, raw,
                         reason=None if m else SPEC_UNCLEAR)
        elif role == 'refresh':
            m = _HERTZ.fullmatch(raw)
            self.add(specs['refresh'], int(m.group(1)) if m else None, pointer, raw,
                     reason=None if m else SPEC_UNCLEAR)

    def offer_state(self, attributes, offer, ld_index, combo):
        """Price, online stock and purchasability; selectable options leave all three unknown."""
        if combo:
            option, value_id, label = combo[0]
            pointer = json_pointer('options', f'attribute[{option.attribute}]', value_id)
            for field in ('price', 'stock', 'purchasable'):
                self.fields.pop(field, None)
                self.add(field, None, pointer, label, reason=OPTION_PRICED)
            return
        base = '/BCData/product_attributes'
        if type(attributes.get('purchasable')) is bool:
            self.add('purchasable', attributes['purchasable'], base + '/purchasable')
        else:
            self.add('purchasable', None, base, reason=NOT_PUBLISHED)
        backorder = attributes.get('unlimited_backorder') is True or (
            type(attributes.get('available_for_backorder')) is int and attributes['available_for_backorder'] > 0)
        if type(attributes.get('instock')) is bool:
            self.add('stock', None if backorder else attributes['instock'], base + '/instock',
                     reason=BACKORDER if backorder else None)
        else:  # a merchant text field alone never establishes stock
            self.add('stock', None, base, reason=NOT_PUBLISHED)
        availability = offer.get('availability')
        if isinstance(availability, str):
            value = _SCHEMA_STOCK.get(availability.casefold().replace('http://', 'https://'))
            self.add('stock', value, json_pointer('ld+json', ld_index, 'offers', 'availability'),
                     reason=None if value is not None else AVAILABILITY_UNCLEAR)
        self.price(attributes, offer, ld_index)

    def price(self, attributes, offer, ld_index):
        pointer = '/BCData/product_attributes/price'
        price = attributes.get('price') if isinstance(attributes.get('price'), dict) else {}
        current = price.get('without_tax')
        if isinstance(current, dict):
            value, reason = self.money(current.get('value'), current.get('currency'))
            self.add('price', value, pointer + '/without_tax', reason=reason)
        else:
            self.add('price', None, pointer, reason=PRICE_ABSENT)
        if 'price' in offer:
            raw, ld_pointer = offer.get('price'), json_pointer('ld+json', ld_index, 'offers', 'price')
            if raw == '' or raw is None:
                self.add('price', None, ld_pointer, reason=PRICE_ABSENT)
            else:
                value, reason = self.money(raw, offer.get('priceCurrency'))
                self.add('price', value, ld_pointer, reason=reason)
        purchasable = [v for v, _, r in self.fields['purchasable'] if r is None]
        call = attributes.get('call_for_price_message')
        if isinstance(call, str) and call.strip():
            reason = CALL_FOR_PRICE
        elif purchasable != [True]:
            reason = NOT_PURCHASABLE
        else:
            return
        self.fields['price'] = [(None, receipt, r or reason) for _, receipt, r in self.fields['price']]

    @staticmethod
    def money(raw, currency):
        """A Decimal amount (at most cents) and currency, or the fixed reason it cannot be one."""
        if isinstance(raw, bool) or not isinstance(raw, (int, Decimal, str)) or currency not in CURRENCIES:
            return None, PRICE_INVALID
        if isinstance(raw, str) and not re.fullmatch(r'[0-9]{1,12}(?:\.[0-9]{1,2})?', raw.strip()):
            return None, PRICE_INVALID
        try:
            amount = Decimal(raw.strip()) if isinstance(raw, str) else Decimal(raw)
            valid = amount.is_finite() and 0 < amount < Decimal(10) ** 12 and amount == amount.quantize(Decimal('0.01'))
        except ArithmeticError:
            valid = False
        if not valid:
            return None, PRICE_INVALID
        return {'amount': format(amount.normalize(), 'f'), 'currency': currency}, None

    def store_notices(self):
        """Store-wide VAT footer and marketplace notice as source-scoped text, and the VAT marker.

        The listed price excludes VAT, so the tax charge is unknown with a reason: never zero and
        never computed here. Without the footer the tax treatment is unpublished, also unknown.
        """
        footer = _text(''.join(self.page.footer))
        statements = [_text(m.group(0)) for m in _VAT_FOOTER.finditer(footer)]
        extra = []
        if statements:
            self.notice('policy.tax', statements, '/footer')
            receipt = self.receipt('costs.order.tax', '/footer', statements[0], scope='source')
            extra.append(receipt)
            tax = Charge(kind='tax', state='unknown', reason=TAX_EXCLUDED, evidence_ids=(receipt.evidence_id,))
        else:
            tax = Charge(kind='tax', state='unknown', reason=TAX_UNPUBLISHED)
        notices = []
        for region, chunks in self.page.regions.items():
            if region.endswith('--global'):
                text = _text(''.join(chunks))
                notices.extend(_text(m.group(0)) for p in _MARKETPLACE_NOTICE for m in p.finditer(text))
        if notices:
            self.notice('policy.marketplace', list(dict.fromkeys(notices)), '/regions/global')
        return (tax,), extra

    def notice(self, field, statements, pointer):
        statements = list(dict.fromkeys(statements))
        receipts = tuple(self.receipt(field, f'{pointer}/{i}', s, scope='source') for i, s in enumerate(statements))
        self.notices[field] = [(' | '.join(statements), r, None) for r in receipts]

    def party(self, field, readings, unnamed):
        if not readings:
            receipt = self.receipt(field, '/custom-fields')
            return PartyObservation(Fact[str](state='unknown', reason=unnamed, evidence_ids=[receipt.evidence_id]),
                                    None, (receipt,))
        entries = []
        for raw, pointer in readings:
            name = _text(raw)
            valid = _PARTY_NAME.fullmatch(name) is not None
            entries.append((name if valid else None, self.receipt(field, pointer, raw),
                            None if valid else PARTY_INVALID))
        fact, receipts = _compose(entries)
        if fact['state'] == 'conflict':
            # Two names of the operator are one party; any other two names are a conflict.
            ids = {party_id(v) for v in fact['alternatives']}
            if len(ids) == 1:
                fact = {'state': 'known', 'value': fact['alternatives'][0], 'evidence_ids': fact['evidence_ids']}
        known = fact['state'] == 'known'
        return PartyObservation(Fact[str].model_validate(fact), party_id(fact['value']) if known else None,
                                receipts)

    def warranty(self, readings):
        entries = []
        tab = _text(''.join(self.page.warranty))
        if tab:
            months = _months(tab)
            entries.append((months, self.receipt('warranty.months', '/tabs/warranty', tab),
                            None if months is not None else WARRANTY_UNCLEAR))
        for raw, pointer in readings:
            months = _months(raw)
            entries.append((months, self.receipt('warranty.months', pointer, raw),
                            None if months is not None else WARRANTY_UNCLEAR))
        if entries:
            fact, receipts = _compose(entries)
        else:
            receipt = self.receipt('warranty.months', '/tabs')
            fact, receipts = {'state': 'unknown', 'reason': WARRANTY_ABSENT,
                              'evidence_ids': [receipt.evidence_id]}, (receipt,)
        provider = self.receipt('warranty.provider', '/custom-fields')
        territory = self.receipt('warranty.territory', '/custom-fields')
        warranty = Warranty(
            months=fact,
            provider={'state': 'unknown', 'reason': WARRANTY_PROVIDER, 'evidence_ids': [provider.evidence_id]},
            territory={'state': 'unknown', 'reason': WARRANTY_TERRITORY, 'evidence_ids': [territory.evidence_id]})
        return WarrantyObservation(warranty, (*receipts, provider, territory))

    def identifiers(self, attributes, ld, ld_index):
        """SKU, MPN, UPC and GTIN when the embedded blocks agree, each with its receipts."""
        found, receipts = [], []
        for key in ('sku', 'mpn', 'upc', 'gtin'):
            sources = [(v.strip(), pointer) for v, pointer in (
                (attributes.get(key), f'/BCData/product_attributes/{key}'),
                (ld.get(key), json_pointer('ld+json', ld_index, key))) if isinstance(v, str) and v.strip()]
            if len({v for v, _ in sources}) == 1:
                found.append((key, sources[0][0]))
                receipts.extend(self.receipt(f'identifiers.{key}', pointer, value) for value, pointer in sources)
        return tuple(found), receipts


def _extraction(fields):
    observations = []
    for entries in fields.values():
        fact, receipts = _compose(entries)
        observations.append(FieldObservation(fact=fact, evidence=receipts))
    return Extraction(observations=tuple(observations))


def _statement(*parts):
    return re.compile(r'\s*'.join(parts), re.IGNORECASE)


POLICY_TOPICS = ('marketplace', 'warranty', 'tax', 'delivery', 'pickup', 'payment')
_TERMS = (
    ('marketplace', _statement(r'Products on ayoubcomputers\.com are sold by one of two parties:',
                               r'Ayoub Computers \(the platform operator, selling its own inventory\) or an approved',
                               r'third-party vendor \(an independent seller registered on the marketplace\)\.')),
    ('marketplace', _statement(r'The seller is clearly identified on the product page and in your order',
                               r'confirmation\.')),
    ('marketplace', _statement(r'The respective vendor is responsible for order processing, invoicing, and',
                               r'delivery of their products\.')),
    ('warranty', _statement(r'Warranties are fulfilled by authorised third-party service partners, brand service',
                            r'centers, or directly by Ayoub Computers \S{1,3} depending on the product.s sourcing and',
                            r'distribution\.')),
    ('warranty', _statement(r'ayoubcomputers\.com acts as the warranty guarantor for every product listed on the',
                            r'platform\.')),
    ('tax', _statement(r'All prices displayed on ayoubcomputers\.com are exclusive of VAT', r'\.')),
    ('tax', _statement(r'Customers residing in Lebanon are subject to an additional 11% Value Added Tax \(VAT\)',
                       r', which will be calculated and applied to the total order value at checkout\.')),
    ('pickup', _statement(r'Certain items are available for online purchase and are shipped directly from vendors',
                          r'or remote warehouses\.', r'These products are not immediately available at all retail',
                          r'locations\.')),
    ('pickup', _statement(r'For collections at authorised retail partner locations, please confirm availability',
                          r'directly with that branch prior to visiting\.')),
    ('payment', _statement(r'Card payment \S{1,3} local and international debit/credit cards accepted')),
    ('payment', _statement(r'Cash on Delivery \(COD\) \S{1,3} accepted in USD or LBP at the daily market rate\.')),
)
_DELIVERY = (
    ('delivery', _statement(r'Free delivery to every district in Lebanon \S{1,3} no minimum order, no hidden',
                            r'charges\.')),
    ('delivery', _statement(r'Zone [ABC] [0-9] \S{1,3} [0-9] Business Days')),
    ('delivery', _statement(r'All delivery timelines are measured in business days and exclude weekends',
                            r'\(Saturday and Sunday\)\.')),
    ('delivery', _statement(r'our logistics partners are currently unable to deliver to villages within',
                            r'[0-9]\S{1,3}[0-9] km of the border', r'\.')),
)

CATALOG = AyoubCatalog()
