"""Ayoub Computers marketplace reader (T19): bounded BigCommerce search and product pages.

ayoubcomputers.com is a multi-vendor marketplace: a listing may be sold and fulfilled by Ayoub
Computers or by a third-party vendor. Each listing is a separate BigCommerce product, so two
listings of one model stay two candidates with their own seller, never one merged seller.

Facts come only from the listing's own structured data: the BCData and JSON-LD blocks (price,
online stock, purchasability, condition), the details list and the custom-field table
(configuration, condition, warranty, seller and fulfiller fields). The marketplace notice and
the VAT footer are store-wide template text: they are kept as source-scoped policy statements
and never become a listing's seller, fulfiller or cost. Titles are not parsed for
configuration. Online stock is not branch stock, and no branch facts are produced.

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

from app.adapters.base import Snapshot, excerpt, tool_result
from app.adapters.woo_catalog import CatalogCandidate, CatalogResult
from app.contracts import Fact, ToolError, ToolResult, Warranty
from app.contracts_v2 import Evidence
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
CONFLICT = 'source values disagree for this listing'

_SEARCH_TEXT = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9._-]*( [a-zA-Z0-9][a-zA-Z0-9._-]*)*')
_PRODUCT_ID = re.compile(r'[1-9][0-9]{0,9}')
_SLUG_PATH = re.compile(r'/[a-z0-9][a-z0-9-]{0,199}/')
_ATTRIBUTE = re.compile(r'attribute\[([1-9][0-9]{0,9})\]')
_GB = re.compile(r'([1-9][0-9]{0,4})\s*(GB|TB)', re.IGNORECASE)
_DESCRIPTION_GB = re.compile(r'([1-9][0-9]{0,4})\s*(GB|TB)(?:\s+(?:SSD|RAM|Unified Memory))?\.?',
                             re.IGNORECASE)
_DESCRIPTION_LINE = re.compile(r'(RAM|Memory|Storage)\s*:\s*(.+)', re.IGNORECASE)
_INCHES = re.compile(r'([1-9][0-9]?(?:\.[0-9])?)\s*(?:"|”|\'\'|inch|inches)?', re.IGNORECASE)
_RESOLUTION = re.compile(r'([1-9][0-9]{2,4})\s*[xX×]\s*([1-9][0-9]{2,4})')
_HERTZ = re.compile(r'([1-9][0-9]{0,3})\s*Hz', re.IGNORECASE)
_PERIOD = re.compile(r'([1-9][0-9]?)\s*(years?|yrs?|months?)', re.IGNORECASE)
_PARTY_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .&'-]{0,79}")
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
    """The parts of a BigCommerce product page this reader uses, with their content regions."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.json_ld: list[str] = []
        self.scripts: list[str] = []
        self.canonical: list[str] = []
        self.product_ids: list[str] = []
        self.info: list[tuple[str, str]] = []
        self.fields: list[tuple[str, str]] = []
        self.warranty: list[str] = []
        self.options: list[_Option] = []
        self.unsupported_inputs = 0
        self.regions: dict[str, list[str]] = {}
        self.footer: list[str] = []
        self.visible: list[str] = []
        self._script: list[str] | None = None
        self._script_ld = False
        self._hidden = 0
        self._divs: list[dict] = []
        self._dl = False
        self._dt: list[str] | None = None
        self._dd: list[str] | None = None
        self._pending_dt: str | None = None
        self._table = False
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._label: list[str] | None = None
        self._select: str | None = None
        self._option_value: tuple[str, list[str]] | None = None
        self._footer = 0

    def _flag(self, name):
        return any(d.get(name) for d in self._divs)

    def _region(self):
        for d in reversed(self._divs):
            if 'region' in d:
                return d['region']
        return None

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
            marker = {}
            if 'data-content-region' in values:
                marker['region'] = values['data-content-region']
            if values.get('id') == 'tab-warranty':
                marker['warranty_tab'] = True
            elif 'productView-description-tabContent' in classes and self._flag('warranty_tab'):
                marker['warranty'] = True  # the tab's text, without its heading and collapse links
            if 'data-product-option-change' in values:
                marker['options'] = True
            if 'form-field' in classes and self._flag('options') and 'data-product-attribute' in values:
                self.options.append(_Option('', values['data-product-attribute']))
                marker['option'] = True
            self._divs.append(marker)
        elif tag == 'dl' and 'productView-info' in classes:
            self._dl = True
        elif tag == 'dt' and self._dl:
            self._dt = []
        elif tag == 'dd' and self._dl:
            self._dd = []
        elif tag == 'table' and 'productView-addition-table' in classes:
            self._table = True
        elif tag == 'tr' and self._table:
            self._row = []
        elif tag == 'td' and self._row is not None:
            self._cell = []
        elif tag == 'label' and self._flag('option') and self.options and not self.options[-1].name_done:
            self._label = []
        elif tag in {'span', 'small'} and self._label is not None and (
                tag == 'small' or 'data-option-value' in values):
            self._finish_label()
        elif tag == 'input':
            if values.get('name') == 'product_id' and values.get('type', '').lower() == 'hidden':
                self.product_ids.append(values.get('value', ''))
            elif self._flag('options'):
                self._option_input(values)
        elif tag == 'select' and self._flag('options'):
            match = _ATTRIBUTE.fullmatch(values.get('name', ''))
            if match is None or not self.options or not self._flag('option'):
                self.unsupported_inputs += 1
            else:
                self.options[-1].attribute = self.options[-1].attribute or match.group(1)
                if self.options[-1].attribute != match.group(1):
                    self.options[-1].supported = False
                self._select = match.group(1)
        elif tag == 'option' and self._select is not None:
            self._option_value = (values.get('value', ''), [])
        elif tag == 'textarea' and self._flag('options'):
            self.unsupported_inputs += 1

    def _option_input(self, values):
        kind = values.get('type', '').lower()
        match = _ATTRIBUTE.fullmatch(values.get('name', ''))
        if kind != 'radio' or match is None or not self.options or not self._flag('option'):
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
        if tag == 'div' and self._divs:
            self._divs.pop()
        elif tag == 'dt' and self._dt is not None:
            self._pending_dt = _text(''.join(self._dt))
            self._dt = None
        elif tag == 'dd' and self._dd is not None:
            if self._pending_dt is not None:
                self.info.append((self._pending_dt.rstrip(':').strip(), _text(''.join(self._dd))))
            self._pending_dt, self._dd = None, None
        elif tag == 'dl':
            self._dl = False
        elif tag == 'td' and self._cell is not None:
            self._row.append(_text(''.join(self._cell)))
            self._cell = None
        elif tag == 'tr' and self._row is not None:
            if len(self._row) == 2:
                self.fields.append((self._row[0].rstrip(':').strip(), self._row[1]))
            elif self._row:
                self.fields.append(('', ''))  # a malformed row is reported, never guessed
            self._row = None
        elif tag == 'table':
            self._table = False
        elif tag == 'label' and self._label is not None:
            self._finish_label()
        elif tag == 'option' and self._option_value is not None:
            value, label = self._option_value
            if value:
                self.options[-1].values.append((value, _text(''.join(label))))
            self._option_value = None
        elif tag == 'select':
            self._select = None

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
        if self._flag('warranty'):
            self.warranty.append(data)
        region = self._region()
        if region is not None:
            self.regions.setdefault(region, []).append(data)
        if self._footer:
            self.footer.append(data)


def _read_page(body: bytes) -> _ProductPage:
    page = _ProductPage()
    page.feed(body.decode('utf-8', errors='replace'))
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
    seller: PartyObservation | None = None
    fulfiller: PartyObservation | None = None
    warranty: WarrantyObservation | None = None
    identifiers: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class PolicyResult:
    extraction: Extraction
    tool_result: ToolResult
    snapshots: tuple[Snapshot, ...]
    missing_topics: tuple[str, ...]


def party_id(name: str) -> str:
    """The operator's own ID, or a vendor ID unique to the vendor's name."""
    key = _key(name)
    if key in _OPERATOR_NAMES:
        return OPERATOR_ID
    return f'{OPERATOR_ID}.vendor.' + '-'.join(re.findall(r'[a-z0-9]+', name.casefold()))


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
        for pid, card_url in targets:
            page = get(self.product_url(pid), 'product')
            if page is None:
                continue
            try:
                read = self._extract(page, pid, card_url, category, run, wanted, corpus_version)
            except (_SchemaError, ValueError, TypeError, KeyError, ArithmeticError, RecursionError,
                    AssertionError):
                errors.append(self._error('unsupported or conflicting product data'))
                continue
            if read is None:
                stats['skipped_not_category'] += 1
                continue
            candidates.extend(read)
        if wanted is not None and wanted - {(c.product_id, c.variant_id) for c in candidates}:
            errors.append(self._error('requested configuration is no longer available for re-reading'))
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
        if len(page.fields) > self.max_fields or ('', '') in page.fields:
            raise _SchemaError('custom fields are malformed or unbounded')
        if page.unsupported_inputs or any(not o.supported or o.kind not in _SUPPORTED_OPTIONS
                                          or not o.attribute or not o.values for o in page.options):
            raise _SchemaError('product options are not plain required choices')
        combos = list(itertools.product(*[[(o, vid, label) for vid, label in o.values] for o in page.options]))
        if len(combos) > self.max_variants:
            raise ValueError('variant bound exceeded')
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

        Statements are verbatim and source-scoped. They never resolve an offer's cost, seller,
        branch stock or warranty provider; topics with no recognized statement are named gaps.
        """
        outcomes, snapshots, fields = [], [], {}
        for url, patterns in ((TERMS_URL, _TERMS), (DELIVERY_URL, _DELIVERY)):
            outcome = fetch(SOURCE_ID, url, expect='html')
            outcomes.append(outcome)
            if outcome.errors or outcome.body is None:
                continue
            snapshots.append(Snapshot.of(outcome))
            text = _text(''.join(_read_page(outcome.body).visible))
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
                        adapter_version=POLICY_VERSION, excerpt=excerpt(statement), origin='runtime',
                        run=run, corpus_version=corpus_version)
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
    for part in (candidate.seller, candidate.fulfiller, candidate.warranty):
        if part is not None:
            receipts.extend(part.evidence)
    return receipts


class _Listing:
    """One configuration of one listing: its readings, then its facts."""

    def __init__(self, catalog, outcome, page, pid, vid, category, run, corpus_version):
        self.catalog, self.outcome, self.page = catalog, outcome, page
        self.pid, self.vid, self.category = pid, vid, category
        self.run, self.corpus_version = run, corpus_version
        self.fields: dict[str, list] = {}

    def receipt(self, field, pointer, text=None, *, scope='variant'):
        o = self.outcome
        identity = '/'.join((o.response_sha256, o.observed_at.isoformat(), self.pid, self.vid, field, pointer,
                             text or '', str(self.run), self.corpus_version))
        variant = scope == 'variant'
        return Evidence(
            evidence_id=sha256(identity.encode()).hexdigest(), source_id=SOURCE_ID,
            product_id=self.pid if variant else None, variant_id=self.vid if variant else None, scope=scope,
            field_path=field, origin_url=o.origin_url, final_url=o.final_url, observed_at=o.observed_at,
            response_sha256=o.response_sha256, adapter_version=ADAPTER_VERSION, json_pointer=pointer,
            excerpt=None if text is None else excerpt(text), origin='runtime', run=self.run,
            corpus_version=self.corpus_version)

    def add(self, field, value, pointer, text=None, *, reason=None):
        self.fields.setdefault(field, []).append((value, self.receipt(field, pointer, text), reason))

    def read(self, attributes, ld, ld_index, combo, listing_url, name):
        specs = _SPEC_FIELDS[self.category]
        roles = {'seller': [], 'fulfiller': [], 'warranty': []}
        for index, (label, raw) in enumerate(self.page.fields):
            role = _FIELD_ROLES.get(_key(label))
            pointer, text = f'/custom-fields/{index}', f'{label}: {raw}'
            if role in roles:
                roles[role].append((raw, pointer, text))
            elif role == 'condition':
                value = raw.casefold() if raw.casefold() in {'new', 'used', 'refurbished'} else None
                self.add('condition', value, pointer, text, reason=None if value else CONDITION_UNCLEAR)
            elif role == 'availability':
                value = {'in stock': True, 'out of stock': False}.get(_text(raw).casefold())
                if value is not None:
                    self.add('stock', value, pointer, text)
            elif role == 'brand' and raw:
                self.add('variant.product.manufacturer', raw, pointer, text)
            elif role == 'color' and raw:
                self.add('variant.color', raw, pointer, text)
            else:
                self.spec(role, raw, pointer, text, specs)
        if self.category == 'laptop' and isinstance(ld.get('description'), str):
            for line in ld['description'].splitlines():
                match = _DESCRIPTION_LINE.fullmatch(line.strip())
                gb = match and _DESCRIPTION_GB.fullmatch(match.group(2).strip())
                if gb:
                    role = 'ram' if match.group(1).casefold() in {'ram', 'memory'} else 'storage'
                    self.add(specs[role], _gigabytes(gb), f'/ld+json/{ld_index}/description', line.strip())
        brand = ld.get('brand')
        if isinstance(brand, dict) and isinstance(brand.get('name'), str) and brand['name'].strip():
            self.add('variant.product.manufacturer', brand['name'].strip(), f'/ld+json/{ld_index}/brand/name')
        if name:
            self.add('variant.product.model', name, f'/ld+json/{ld_index}/name')
        for index, (label, raw) in enumerate(self.page.info):
            if _key(label) == 'condition':
                value = raw.casefold() if raw.casefold() in {'new', 'used', 'refurbished'} else None
                self.add('condition', value, f'/details/{index}', f'{label}: {raw}',
                         reason=None if value else CONDITION_UNCLEAR)
        offer = ld.get('offers') if isinstance(ld.get('offers'), dict) else {}
        condition = offer.get('itemCondition')
        if isinstance(condition, str):
            value = _SCHEMA_CONDITION.get(condition.casefold().replace('http://', 'https://'))
            self.add('condition', value, f'/ld+json/{ld_index}/offers/itemCondition',
                     reason=None if value else CONDITION_UNCLEAR)
        for option, value_id, label in combo:
            pointer = f'/options/attribute[{option.attribute}]/{value_id}'
            if _key(option.name) in {'color', 'colour'} and label:
                self.add('variant.color', label, pointer, f'{option.name}: {label}')
            else:
                self.spec(_FIELD_ROLES.get(_key(option.name)), label, pointer, f'{option.name}: {label}', specs)
        if any(not any(r is None for _, _, r in self.fields.get(f, [])) for f in _REQUIRED_SPECS[self.category]):
            return None  # the page does not carry this category's mandatory specifications
        self.offer_state(attributes, offer, ld_index, combo)
        self.store_notices()
        observations = []
        for field, entries in self.fields.items():
            fact, receipts = _compose(entries)
            observations.append(FieldObservation(fact=fact, evidence=receipts))
        return MarketplaceCandidate(
            SOURCE_ID, self.pid, self.vid, self.category, name or self.pid, listing_url,
            Extraction(observations=tuple(observations)),
            tuple((o.name, label) for o, _, label in combo),
            seller=self.party('seller', roles['seller'], SELLER_UNNAMED),
            fulfiller=self.party('fulfiller_id', roles['fulfiller'], FULFILLER_UNNAMED),
            warranty=self.warranty(roles['warranty']),
            identifiers=self.identifiers(attributes, ld))

    def spec(self, role, raw, pointer, text, specs):
        if role in {'cpu', 'gpu', 'os'} and role in specs and raw:
            self.add(specs[role], raw, pointer, text)
        elif role in {'ram', 'storage'} and role in specs and (m := _GB.fullmatch(raw.strip())):
            self.add(specs[role], _gigabytes(m), pointer, text)
        elif role == 'size' and (m := _INCHES.fullmatch(raw.strip())):
            self.add(specs['size'], m.group(1), pointer, text)
        elif role == 'resolution' and (m := _RESOLUTION.fullmatch(raw.strip())):
            self.add(specs['width'], int(m.group(1)), pointer, text)
            self.add(specs['height'], int(m.group(2)), pointer, text)
        elif role == 'refresh' and (m := _HERTZ.fullmatch(raw.strip())):
            self.add(specs['refresh'], int(m.group(1)), pointer, text)

    def offer_state(self, attributes, offer, ld_index, combo):
        """Price, online stock and purchasability; selectable options leave all three unknown."""
        if combo:
            option, value_id, label = combo[0]
            pointer, text = f'/options/attribute[{option.attribute}]/{value_id}', f'{option.name}: {label}'
            for field in ('price', 'stock', 'purchasable'):
                self.fields.pop(field, None)
                self.add(field, None, pointer, text, reason=OPTION_PRICED)
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
            self.add('stock', value, f'/ld+json/{ld_index}/offers/availability',
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
            raw = offer.get('price')
            if raw == '' or raw is None:
                self.add('price', None, f'/ld+json/{ld_index}/offers/price', reason=PRICE_ABSENT)
            else:
                value, reason = self.money(raw, offer.get('priceCurrency'))
                self.add('price', value, f'/ld+json/{ld_index}/offers/price', reason=reason)
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
        """Store-wide VAT footer and marketplace notice, kept as source-scoped policy text."""
        footer = _text(''.join(self.page.footer))
        statements = [_text(m.group(0)) for m in _VAT_FOOTER.finditer(footer)]
        if statements:
            self.notice('policy.tax', statements, '/footer')
        notices = []
        for region, chunks in self.page.regions.items():
            if region.endswith('--global'):
                text = _text(''.join(chunks))
                notices.extend(_text(m.group(0)) for p in _MARKETPLACE_NOTICE for m in p.finditer(text))
        if notices:
            self.notice('policy.marketplace', list(dict.fromkeys(notices)), '/regions/global')

    def notice(self, field, statements, pointer):
        value = ' | '.join(dict.fromkeys(statements))
        receipt = self.receipt(field, pointer, value, scope='source')
        self.fields[field] = [(value, receipt, None)]

    def party(self, field, readings, unnamed):
        if not readings:
            receipt = self.receipt(field, '/custom-fields')
            return PartyObservation(Fact[str](state='unknown', reason=unnamed, evidence_ids=[receipt.evidence_id]),
                                    None, (receipt,))
        entries = []
        for raw, pointer, text in readings:
            name = _text(raw)
            valid = _PARTY_NAME.fullmatch(name) is not None
            entries.append((name if valid else None, self.receipt(field, pointer, text),
                            None if valid else PARTY_INVALID))
        fact, receipts = _compose(entries)
        if fact['state'] == 'conflict':
            # Two spellings of one party are still one party; two parties are a conflict.
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
        for raw, pointer, text in readings:
            months = _months(raw)
            entries.append((months, self.receipt('warranty.months', pointer, text),
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

    def identifiers(self, attributes, ld):
        found = []
        for key in ('sku', 'mpn', 'upc', 'gtin'):
            values = {v.strip() for v in (attributes.get(key), ld.get(key)) if isinstance(v, str) and v.strip()}
            if len(values) == 1:
                found.append((key, values.pop()))
        return tuple(found)


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
