"""Shared WooCommerce store reader (MVP 05).

One product read is two gate requests: the Store API slug query, which returns the parent
product (attributes, terms and variation list) and one variation record, then the product page,
whose variation form embeds every variation's price and stock. Store modules supply the
allow-listed URLs and read their own page wording into normalized claims.

Store text is data. It stays in raw attributes, evidence excerpts and snapshots; reasons and
error messages use the fixed wording below plus normalized values.
"""
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
import html
from html.parser import HTMLParser
import json
import re
from types import MappingProxyType
from typing import Any, Literal

from pydantic import ValidationError

from app.adapters.base import (
    AdapterResult, CandidateQuery, Fetch, PolicyDocument, PolicyFact, Snapshot, arrival_window,
    json_pointer, make_evidence, minor_units, offer_id, parse_offer_id, policy_evidence, tool_result,
)
from app.contracts import ArrivalWindow, Evidence, Model, Offer, Sim, ToolError
from app.devices import canonical_manufacturer, find_manufacturers, match_model, storage_tokens
from app.identity import known_model
from app.sources import FetchOutcome

# Attribute names are compared in this normalized form: 'pa_storage-capacity' reads 'storage capacity'.
COLOR_NAMES = frozenset({'color', 'colour'})
STORAGE_NAMES = frozenset({'storage', 'storage capacity', 'capacity', 'm size', 'memory', 'size'})
SIM_NAMES = frozenset({'sim', 'sim type', 'esim'})
CONDITION_NAMES = frozenset({'condition'})
BRAND_NAMES = frozenset({'brand', 'manufacturer'})
# Not 'version': stores use it for a region or spec ('Middle East Version').
MODEL_NAMES = frozenset({'model', 'phone model'})
CONDITIONS = frozenset({'new', 'used', 'refurbished'})
CURRENCY = re.compile(r'[A-Z]{3}')
DIGITS = re.compile(r'[0-9]+')
STORAGE_OPTION = re.compile(r'\s*([0-9]{1,4})\s*(GB|TB)\s*', re.IGNORECASE)
# WooCommerce's classes on a variation's availability line.
BACKORDER_CLASS = re.compile(r'(?<![\w-])available-on-backorder(?![\w-])')
IN_STOCK_CLASS = re.compile(r'(?<![\w-])in-stock(?![\w-])')
MAX_RAW_TEXT = 300  # characters kept per raw store string; raw attributes are informative only
MAX_RAW_ITEMS = 20
MAX_POLICY_DAYS = 60

# Text inside these is never shown; the rest is joined, breaking at block elements.
HIDDEN_TAGS = frozenset({'script', 'style', 'noscript', 'svg', 'template', 'head'})
BREAK_TAGS = frozenset({
    'address', 'article', 'aside', 'blockquote', 'br', 'button', 'dd', 'details', 'div', 'dl', 'dt',
    'fieldset', 'figcaption', 'figure', 'footer', 'form', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'header',
    'hr', 'label', 'li', 'main', 'nav', 'ol', 'option', 'p', 'pre', 'section', 'select', 'summary',
    'table', 'tbody', 'td', 'tfoot', 'th', 'thead', 'tr', 'ul',
})

# Why a fact stays unknown or conflicting. Fixed wording only, so reasons are safe to show the planner.
NOT_ON_PAGE = 'variation is not on the product page'
PAGE_UNREAD = 'product page could not be read'
FORM_UNREAD = 'product page variation data could not be read'
PRICE_NOT_NUMBER = 'page price is not a number'
PRICE_INVALID = 'page price is not a valid amount'
PRICE_DECIMALS = 'price has more decimals than the currency allows'
PRICE_ZERO = 'store lists a zero price'
NO_CURRENCY = 'currency is not declared'
PRICE_CONFLICT = 'store reports different prices for this variation'
PURCHASABLE_UNKNOWN = 'purchasability is not stated'
PURCHASABLE_CONFLICT = 'store reports different purchasability for this variation'
STOCK_UNKNOWN = 'stock is not stated'
STOCK_CONFLICT = 'store reports different stock for this variation'
STOCK_BACKORDER = 'variation is on backorder'
STOCK_MAYBE_BACKORDER = 'variation may be on backorder'
MANUFACTURER_UNKNOWN = 'manufacturer is not published'
MANUFACTURER_CONFLICT = 'product data names different manufacturers'
MODEL_UNKNOWN = 'product name does not name a known model'
MODEL_ANY = 'variation accepts any model option'
MODEL_UNREADABLE = 'model option is not a known model'
MODEL_CONFLICT = 'model option and product name disagree'
STORAGE_UNKNOWN = 'storage is not published'
STORAGE_UNREADABLE = 'storage option is not a recognized size'
STORAGE_SEVERAL = 'product name lists several storage sizes'
STORAGE_CONFLICT = 'storage option and product name disagree'
STORAGE_ANY = 'variation accepts any storage option'
COLOR_UNKNOWN = 'color is not published'
COLOR_UNREADABLE = 'color option is empty'
COLOR_CONFLICT = 'store reports different colors for this variation'
COLOR_ANY = 'variation accepts any color option'
SIM_UNKNOWN = 'SIM type is not published'
SIM_NOT_STATED = 'SIM type is not stated for this option'
SIM_UNRECOGNIZED = 'SIM option is not recognized'
SIM_CONFLICT = 'store reports different SIM types for this variation'
SIM_ANY = 'variation accepts any SIM option'
SIM_NAME_UNCONFIRMED = 'SIM type in the product name is not confirmed'
REGION_UNKNOWN = 'region is not published'
CONDITION_UNKNOWN = 'condition is not published'
CONDITION_UNRECOGNIZED = 'condition option is not recognized'
CONDITION_CONFLICT = 'store reports different conditions for this variation'
CONDITION_ANY = 'variation accepts any condition option'
WARRANTY_PROVIDER_UNKNOWN = 'warranty provider is not published'
WARRANTY_MONTHS_UNKNOWN = 'warranty length is not published'
WARRANTY_TERRITORY_UNKNOWN = 'warranty territory is not published'
WARRANTY_CONFLICT = 'warranty statements disagree'
PAYMENT_UNKNOWN = 'payment terms are not published on the product page'
CASH_AND_CARD = 'store accepts cash and card; price terms per method are not published'
PAYMENT_CONFLICT = 'payment statements disagree'
DELIVERY_TIME_UNKNOWN = 'delivery time is not published'
PICKUP_READINESS_UNKNOWN = 'pickup readiness is not published'
ARRIVAL_CONFLICT = 'delivery estimates conflict'
BRANCH_STOCK_UNKNOWN = 'online stock does not show branch stock'
VAT_UNKNOWN = 'VAT treatment is not published'
VAT_UNBOUND = 'VAT statement is not tied to a known price'
VAT_DISAGREE = 'VAT statements on the product page disagree'
DELIVERY_FEE_UNKNOWN = 'delivery charge is not published'
FIXED_REASONS = frozenset({
    NOT_ON_PAGE, PAGE_UNREAD, FORM_UNREAD, PRICE_NOT_NUMBER, PRICE_INVALID, PRICE_DECIMALS, PRICE_ZERO,
    NO_CURRENCY, PRICE_CONFLICT, PURCHASABLE_UNKNOWN, PURCHASABLE_CONFLICT, STOCK_UNKNOWN, STOCK_CONFLICT,
    STOCK_BACKORDER,
    STOCK_MAYBE_BACKORDER,
    MANUFACTURER_UNKNOWN, MANUFACTURER_CONFLICT, MODEL_UNKNOWN, MODEL_ANY, MODEL_UNREADABLE, MODEL_CONFLICT,
    STORAGE_UNKNOWN, STORAGE_UNREADABLE,
    STORAGE_SEVERAL, STORAGE_CONFLICT, STORAGE_ANY, COLOR_UNKNOWN, COLOR_UNREADABLE, COLOR_CONFLICT,
    COLOR_ANY, SIM_UNKNOWN, SIM_NOT_STATED, SIM_UNRECOGNIZED, SIM_CONFLICT, SIM_ANY, SIM_NAME_UNCONFIRMED,
    REGION_UNKNOWN,
    CONDITION_UNKNOWN,
    CONDITION_UNRECOGNIZED, CONDITION_CONFLICT, CONDITION_ANY, WARRANTY_PROVIDER_UNKNOWN,
    WARRANTY_MONTHS_UNKNOWN, WARRANTY_TERRITORY_UNKNOWN, WARRANTY_CONFLICT, PAYMENT_UNKNOWN, CASH_AND_CARD,
    PAYMENT_CONFLICT, DELIVERY_TIME_UNKNOWN, PICKUP_READINESS_UNKNOWN, ARRIVAL_CONFLICT, BRANCH_STOCK_UNKNOWN,
    VAT_UNKNOWN, VAT_UNBOUND, VAT_DISAGREE, DELIVERY_FEE_UNKNOWN,
})

# INVALID_SCHEMA messages, after the source ID; fixed wording, never store text.
API_UNREADABLE = 'store API reply is not a product list'
API_PRODUCTS = 'store API reply does not hold exactly one variable product'
PRODUCT_CHANGED = 'product ID differs from the configuration'
PRICE_FIELDS = 'price fields are not in the expected shape'
PRODUCT_FIELDS = 'product fields are not in the expected shape'
VARIATION_FIELDS = 'variation record is not in the expected shape'
PAGE_MARKUP = 'product page markup could not be read'
NO_FORM = 'product page has no variation form for this product'
AJAX_ONLY = 'product page loads variations separately'
FORM_FIELDS = 'product page variation data is not in the expected shape'
OFFER_UNREADABLE = 'a variation could not be read'
UNKNOWN_OFFER = 'unknown offer'
UNKNOWN_POLICY = 'unknown policy'
OFFER_NOT_LISTED = 'offer is not listed'
POLICY_MARKUP = 'policy page markup could not be read'
POLICY_NO_STATEMENTS = 'policy page states nothing the reader recognizes'
ERROR_REASONS = frozenset({
    API_UNREADABLE, API_PRODUCTS, PRODUCT_CHANGED, PRICE_FIELDS, PRODUCT_FIELDS, VARIATION_FIELDS,
    PAGE_MARKUP, NO_FORM, AJAX_ONLY, FORM_FIELDS, OFFER_UNREADABLE, UNKNOWN_OFFER, UNKNOWN_POLICY,
    OFFER_NOT_LISTED, POLICY_MARKUP, POLICY_NO_STATEMENTS,
})


@dataclass(frozen=True)
class ProductEndpoints:
    """One allow-listed product: its Store API slug query and its page.

    key is the store's parent product ID. label is an admission hint, used only to skip a product
    that clearly cannot match the query's model before fetching; identity comes from the data.
    """
    key: str
    api_url: str
    page_url: str
    label: str


@dataclass(frozen=True)
class PolicyPage:
    """A store-wide page, the offer field paths it may settle, and its statement reader.

    settles lists only paths the store's policy_effect can change; delivery_only names those it
    changes for delivery offers alone, so a pickup run is not offered a page that settles nothing.
    """
    policy_id: str
    url: str
    settles: tuple[str, ...]
    extract: Callable[[str], tuple[PolicyFact, ...]]
    delivery_only: tuple[str, ...] = ()


@dataclass(frozen=True)
class Claim:
    """A store statement read into normalized values, with the bounded store text that shows it."""
    value: Any
    text: str
    scope: Literal['source', 'product'] = 'product'


@dataclass(frozen=True)
class PageFacts:
    """What a product page says outside its variation form, as normalized claims."""
    tax_included: tuple[Claim, ...] = ()  # value: the displayed Decimal amount the statement follows
    delivery_fee: Claim | None = None  # value: {'amount': Decimal, 'currency': str, 'conditions': tuple}
    arrival: Claim | None = None  # value: (min_days, max_days, 'calendar' or 'working')
    warranty: Claim | None = None  # value: (provider or None, months or None); provider from our table
    payment_methods: Claim | None = None  # value: a tuple of 'cash' and/or 'card'


WarrantyStatement = tuple[str | None, int | None]


def no_page_facts(text: str) -> PageFacts:
    return PageFacts()


def no_statement(text: str) -> WarrantyStatement | None:
    return None


def no_policy_effect(offer: Offer, policy: PolicyDocument) -> Offer:
    return offer


@dataclass(frozen=True)
class StoreConfig:
    """Everything store-specific: endpoints, attribute names, value tables and statement readers."""
    source_id: str
    adapter_version: str
    products: tuple[ProductEndpoints, ...]
    policies: tuple[PolicyPage, ...] = ()
    color_names: frozenset[str] = COLOR_NAMES
    storage_names: frozenset[str] = STORAGE_NAMES
    sim_names: frozenset[str] = SIM_NAMES
    warranty_names: frozenset[str] = frozenset()
    # Normalized option value (casefolded, single spaces) -> SIM type, or None when the option
    # does not say; values missing from the table are unrecognized.
    sim_map: Mapping[str, Sim | None] = field(default_factory=lambda: MappingProxyType({}))
    # Product-name phrase -> SIM type, or None for a phrase about the SIM whose type is not confirmed.
    name_sim: Mapping[str, Sim | None] = field(default_factory=lambda: MappingProxyType({}))
    page_facts: Callable[[str], PageFacts] = no_page_facts  # reads the product page's visible text
    name_warranty: Callable[[str], WarrantyStatement | None] = no_statement  # reads the product name
    option_warranty: Callable[[str], WarrantyStatement | None] = no_statement  # reads a warranty option
    policy_effect: Callable[[Offer, PolicyDocument], Offer] = no_policy_effect  # what each page settles
    branches: tuple[tuple[str, str], ...] = ()  # (branch_id, city)


class _SchemaError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _reject_constant(name: str):
    raise ValueError(f'{name} is not valid JSON')


def strict_json(text: str):
    """JSON with Decimal numbers; NaN and Infinity are refused, as they must not reach money."""
    return json.loads(text, parse_float=Decimal, parse_constant=_reject_constant)


def normal(text: str) -> str:
    """Casefolded with single spaces, the form value tables use."""
    return ' '.join(text.casefold().split())


def role(name: str) -> str:
    """'storage-capacity', 'pa_storage-capacity' and 'Storage Capacity' all read 'storage capacity'."""
    name = name.casefold()
    name = name[3:] if name.startswith('pa_') else name
    return ' '.join(part for part in re.split(r'[\s_-]+', name) if part)


def form_key(name: str, taxonomy: str | None) -> str:
    """The variation form's key: attribute_<taxonomy>, or the sanitized name ('M Size' -> m-size)."""
    if taxonomy:
        return f'attribute_{taxonomy}'
    slug = re.sub(r'[^a-z0-9_-]+', '', re.sub(r'\s+', '-', name.strip().casefold()))
    return 'attribute_' + re.sub(r'-{2,}', '-', slug)


def phrase(text: str) -> re.Pattern:
    """A whole-word, case-insensitive pattern that tolerates any spacing between the words."""
    words = (re.escape(w) for w in text.split())
    return re.compile(r'(?<![0-9a-z])' + r'\s+'.join(words) + r'(?![0-9a-z])', re.IGNORECASE)


def storage_size(value: str) -> int | None:
    """'256GB', '256 GB' or '1TB' as gigabytes; anything else is not a size."""
    match = STORAGE_OPTION.fullmatch(value)
    if match is None:
        return None
    size = int(match.group(1)) * (1024 if match.group(2).upper() == 'TB' else 1)
    return size or None


def decimal_amount(text: str) -> Decimal:
    """'1,030.00' as Decimal('1030.00')."""
    return Decimal(text.replace(',', ''))


def _places(value: Decimal) -> int:
    """Fractional digits that carry precision; trailing zeros do not."""
    _, digits, exponent = value.as_tuple()
    while exponent < 0 and len(digits) > 1 and digits[-1] == 0:
        digits, exponent = digits[:-1], exponent + 1
    return max(0, -exponent)


def _written_places(value: int | Decimal) -> int:
    return 0 if isinstance(value, int) else max(0, -value.as_tuple().exponent)


def _clip(text: str, limit: int = MAX_RAW_TEXT) -> str:
    return text[:limit]


def _json_bool(value: bool) -> str:
    return 'true' if value else 'false'


def _int_id(value) -> str | None:
    # bool is an int subclass; a JSON true is never an ID.
    return str(value) if type(value) is int and value > 0 else None


def _unescaped(text: str) -> str:
    """Store API names arrive HTML-escaped ('&#8211;'); collapse spacing as the page shows it."""
    return ' '.join(html.unescape(text).split())


class _PageParser(HTMLParser):
    """Visible text, variation forms and JSON-LD blocks, read with the standard library parser."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.hidden: list[str] = []
        self.forms: list[dict[str, str | None]] = []
        self.json_ld: list[str] = []
        self._ld: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        values: dict[str, str | None] = {}
        for name, value in attrs:
            values.setdefault(name, value)  # browsers keep the first of repeated attributes
        if tag == 'body':
            self.hidden = [t for t in self.hidden if t != 'head']  # an unclosed head ends at the body
        elif tag == 'form' and 'variations_form' in (values.get('class') or '').split():
            self.forms.append(values)
        elif tag == 'script' and (values.get('type') or '').strip().lower() == 'application/ld+json':
            self._ld = []
        if tag in HIDDEN_TAGS:
            self.hidden.append(tag)
        elif tag in BREAK_TAGS:
            self.chunks.append(' ')

    def handle_endtag(self, tag):
        if tag == 'script' and self._ld is not None:
            self.json_ld.append(''.join(self._ld))
            self._ld = None
        if tag in HIDDEN_TAGS:
            if tag in self.hidden:
                del self.hidden[len(self.hidden) - 1 - self.hidden[::-1].index(tag):]
        elif tag in BREAK_TAGS:
            self.chunks.append(' ')

    def handle_data(self, data):
        if self._ld is not None:
            self._ld.append(data)
        elif not self.hidden:
            self.chunks.append(data)

    @property
    def text(self) -> str:
        return ' '.join(''.join(self.chunks).split())


def read_html(body: bytes) -> _PageParser:
    """Parse a reply body; raises AssertionError on markup html.parser cannot read."""
    parser = _PageParser()
    parser.feed(body.decode('utf-8', errors='replace'))
    parser.close()
    return parser


def visible_text(body: bytes) -> str:
    """The page text a buyer sees: hidden elements removed, entities decoded, spacing collapsed."""
    return read_html(body).text


def _as_list(value) -> list:
    return value if isinstance(value, list) else [value]


def _ld_nodes(data) -> list[dict]:
    nodes = [n for n in _as_list(data) if isinstance(n, dict)]
    graphs = [n for node in nodes if isinstance(node.get('@graph'), list) for n in node['@graph']]
    return nodes + [n for n in graphs if isinstance(n, dict)]


def vat_flags(blocks, page_url: str) -> set[bool]:
    """valueAddedTaxIncluded values in this page's JSON-LD Product offers."""
    flags = set()
    for block in blocks:
        try:
            data = strict_json(block)
        except (ValueError, RecursionError):
            continue
        for node in _ld_nodes(data):
            if 'Product' not in _as_list(node.get('@type')):
                continue
            offers = [o for o in _as_list(node.get('offers')) if isinstance(o, dict)]
            if node.get('url') != page_url and all(o.get('url') != page_url for o in offers):
                continue
            for offer in offers:
                for spec in _as_list(offer.get('priceSpecification')):
                    if isinstance(spec, dict) and isinstance(spec.get('valueAddedTaxIncluded'), bool):
                        flags.add(spec['valueAddedTaxIncluded'])
    return flags


@dataclass(frozen=True)
class _Attribute:
    index: int  # position in the parent's attributes
    name: str  # as the store writes it
    roles: frozenset[str]  # its name and taxonomy, normalized
    key: str  # its variation-form key
    variation: bool
    terms: tuple[tuple[str, str], ...]  # (slug, name)

    def term_name(self, value: str) -> str:
        """The term name for a slug, or the value itself (custom attributes store names)."""
        return next((name for slug, name in self.terms if slug == value), ' '.join(value.split()))


@dataclass(frozen=True)
class _Api:
    outcome: FetchOutcome
    index: int  # the parent's position in the reply
    record: dict
    product_id: str
    name: str
    minor_unit: int
    currency: str | None
    attributes: tuple[_Attribute, ...]
    options: dict[str, tuple[int, dict[str, tuple[int, str]]]]  # id -> (entry, {name: (position, value)})
    records: dict[str, tuple[int, dict]]  # variation records: id -> (reply index, record)


def _prices(record: dict) -> tuple[int, str | None]:
    prices = record.get('prices')
    minor = prices.get('currency_minor_unit') if isinstance(prices, dict) else None
    if type(minor) is not int or not 0 <= minor <= 6:
        raise _SchemaError(PRICE_FIELDS)
    currency = prices.get('currency_code')
    return minor, currency if isinstance(currency, str) and CURRENCY.fullmatch(currency) else None


def _attributes(raw) -> tuple[_Attribute, ...]:
    if not isinstance(raw, list):
        raise _SchemaError(PRODUCT_FIELDS)
    attributes = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise _SchemaError(PRODUCT_FIELDS)
        name, taxonomy, terms = entry.get('name'), entry.get('taxonomy'), entry.get('terms')
        if (not isinstance(name, str) or not (taxonomy is None or isinstance(taxonomy, str))
                or not isinstance(entry.get('has_variations'), bool) or not isinstance(terms, list)
                or not all(isinstance(t, dict) and isinstance(t.get('name'), str)
                           and isinstance(t.get('slug'), str) for t in terms)):
            raise _SchemaError(PRODUCT_FIELDS)
        name = _unescaped(name)
        roles = frozenset({role(name)} | ({role(taxonomy)} if taxonomy else set()))
        attributes.append(_Attribute(
            i, name, roles, form_key(name, taxonomy), entry['has_variations'],
            tuple((t['slug'], _unescaped(t['name'])) for t in terms)))
    return tuple(attributes)


def _options(raw) -> dict[str, tuple[int, dict[str, tuple[int, str]]]]:
    if not isinstance(raw, list):
        raise _SchemaError(PRODUCT_FIELDS)
    options = {}
    for j, entry in enumerate(raw):
        vid = _int_id(entry.get('id')) if isinstance(entry, dict) else None
        chosen = entry.get('attributes') if isinstance(entry, dict) else None
        if vid is None or not isinstance(chosen, list):
            raise _SchemaError(PRODUCT_FIELDS)
        values = {}
        for k, pair in enumerate(chosen):
            if not (isinstance(pair, dict) and isinstance(pair.get('name'), str)
                    and isinstance(pair.get('value'), str)):
                raise _SchemaError(PRODUCT_FIELDS)
            name, value = _unescaped(pair['name']), html.unescape(pair['value'])
            if values.setdefault(name, (k, value))[1] != value:  # one name, two values
                raise _SchemaError(PRODUCT_FIELDS)
        earlier = options.setdefault(vid, (j, values))[1]
        if {n: v for n, (_, v) in earlier.items()} != {n: v for n, (_, v) in values.items()}:
            raise _SchemaError(PRODUCT_FIELDS)  # a variation listed twice must list the same options
    return options


def parse_api(outcome: FetchOutcome, key: str) -> _Api:
    """The parent product, its attributes and variation list, and any variation records."""
    try:
        data = strict_json(outcome.body.decode('utf-8'))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise _SchemaError(API_UNREADABLE) from exc
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        raise _SchemaError(API_UNREADABLE)
    parents = [(i, r) for i, r in enumerate(data)
               if r.get('type') == 'variable' and type(r.get('parent')) is int and r['parent'] == 0]
    if len(parents) != 1 or _int_id(parents[0][1].get('id')) is None:
        raise _SchemaError(API_PRODUCTS)
    index, parent = parents[0]
    product_id = _int_id(parent['id'])
    if product_id != key:
        raise _SchemaError(PRODUCT_CHANGED)
    minor, currency = _prices(parent)
    if not isinstance(parent.get('name'), str):
        raise _SchemaError(PRODUCT_FIELDS)
    attributes, options = _attributes(parent.get('attributes')), _options(parent.get('variations'))
    records = {}
    for i, record in enumerate(data):
        # Only this product's variations; a record for another parent is not ours to read.
        if (record.get('type') != 'variation' or type(record.get('parent')) is not int
                or record['parent'] != parent['id']):
            continue
        vid = _int_id(record.get('id'))
        try:
            prices = record['prices']
            _prices(record)
            price = prices.get('price')
            if vid is None or not isinstance(price, str) or not DIGITS.fullmatch(price):
                raise _SchemaError(VARIATION_FIELDS)
        except (_SchemaError, KeyError) as exc:
            raise _SchemaError(VARIATION_FIELDS) from exc
        if records.setdefault(vid, (i, record))[1] != record:
            raise _SchemaError(VARIATION_FIELDS)  # a variation returned twice must say the same
    return _Api(outcome, index, parent, product_id, _unescaped(parent['name']), minor, currency,
                attributes, options, records)


@dataclass(frozen=True)
class _PageItem:
    index: int  # position among the page's entries for this product, for evidence tags
    data: dict


@dataclass(frozen=True)
class _Page:
    outcome: FetchOutcome | None
    text: str | None  # None when the page was not read
    json_ld: tuple[str, ...]
    items: dict[str, list[_PageItem]] | None  # None when the variation data could not be read
    reason: str  # why items is None


def _form_items(forms: list[dict], product_id: str) -> dict[str, list[_PageItem]]:
    forms = [f for f in forms if (f.get('data-product_id') or '').strip() == product_id]
    if not forms:
        raise _SchemaError(NO_FORM)
    items: dict[str, list[_PageItem]] = {}
    count = 0
    for form in forms:
        raw = form.get('data-product_variations')
        if raw is not None and raw.strip() == 'false':
            raise _SchemaError(AJAX_ONLY)
        try:
            entries = strict_json(raw) if raw is not None else None
        except (ValueError, RecursionError) as exc:
            raise _SchemaError(FORM_FIELDS) from exc
        if not isinstance(entries, list):
            raise _SchemaError(FORM_FIELDS)
        for entry in entries:
            vid = _int_id(entry.get('variation_id')) if isinstance(entry, dict) else None
            chosen = entry.get('attributes') if isinstance(entry, dict) else None
            if (vid is None or not isinstance(chosen, dict)
                    or not all(isinstance(k, str) and isinstance(v, str) for k, v in chosen.items())):
                raise _SchemaError(FORM_FIELDS)
            same = items.setdefault(vid, [])
            if all(entry != earlier.data for earlier in same):  # identical repeats are one entry
                same.append(_PageItem(count, entry))
            count += 1
    return items


def read_page(outcome: FetchOutcome, product_id: str) -> tuple[_Page, str | None]:
    """The page's text and JSON-LD, and its variation entries; the second value is a schema problem."""
    if outcome.errors or outcome.body is None:
        return _Page(None, None, (), None, PAGE_UNREAD), None
    try:
        parser = read_html(outcome.body)
    except AssertionError:
        return _Page(outcome, None, (), None, PAGE_UNREAD), PAGE_MARKUP
    try:
        items = _form_items(parser.forms, product_id)
    except _SchemaError as exc:
        return _Page(outcome, parser.text, tuple(parser.json_ld), None, FORM_UNREAD), exc.reason
    return _Page(outcome, parser.text, tuple(parser.json_ld), items, FORM_UNREAD), None


def policy_window(observed_at: datetime, value: dict) -> ArrivalWindow | None:
    """A policy fact's {'min_days', 'max_days', 'unit'} as an estimate window, if well formed."""
    low, high, unit = value.get('min_days'), value.get('max_days'), value.get('unit')
    if (type(low) is not int or type(high) is not int or not 0 <= low <= high <= MAX_POLICY_DAYS
            or unit not in ('calendar', 'working')):
        return None
    return arrival_window(observed_at, low, high, unit=unit, basis='estimate')


def merge_policy_fact(offer: Offer, policy: PolicyDocument, fact: PolicyFact, field_path: str, value, *,
                      conflict_reason: str, only_if_unknown: bool = False) -> Offer:
    """One store-wide statement merged into a top-level offer fact, citing its policy page.

    An unknown without evidence becomes known. An unknown that cites a store statement (the page
    says both cash and card, say) stays unknown, citing both, with the conflict reason: a policy
    cannot settle what the page left open. An equal value adds evidence; a different value becomes a
    conflict. With only_if_unknown, a different value or a cited unknown leaves the offer as it was.
    Applying a page twice changes nothing.
    """
    if field_path not in ('payment_basis', 'arrival'):
        raise ValueError('Policy statements settle payment_basis or arrival')
    record = policy_evidence(policy, fact, field_path)
    if any(e.evidence_id == record.evidence_id for e in offer.evidence):
        return offer
    data = offer.model_dump(mode='json')
    current = data[field_path]
    new = value.model_dump(mode='json') if isinstance(value, Model) else value
    ids = [*current.get('evidence_ids', []), record.evidence_id]
    if current['state'] == 'unknown' and not current.get('evidence_ids'):
        data[field_path] = {'state': 'known', 'value': new, 'evidence_ids': [record.evidence_id]}
    elif current['state'] == 'known' and current['value'] == new:
        data[field_path] = {**current, 'evidence_ids': ids}
    elif only_if_unknown:
        return offer
    elif current['state'] == 'unknown':
        data[field_path] = {'state': 'unknown', 'reason': conflict_reason, 'evidence_ids': ids}
    elif current['state'] == 'known':
        data[field_path] = {'state': 'conflict', 'alternatives': [current['value'], new],
                            'reason': conflict_reason, 'evidence_ids': ids}
    else:
        alternatives = current['alternatives'] + ([] if new in current['alternatives'] else [new])
        data[field_path] = {**current, 'alternatives': alternatives, 'evidence_ids': ids}
    data['evidence'].append(record.model_dump(mode='json'))
    return Offer.model_validate(data)


class _OfferBuilder:
    """Builds one variation's offer for the query's route; every fact cites the reply it came from."""

    def __init__(self, adapter: 'WooCommerceAdapter', product: ProductEndpoints, api: _Api, page: _Page,
                 facts: PageFacts | None, query: CandidateQuery, vid: str):
        self.cfg, self.version, self.source_id = adapter.config, adapter.adapter_version, adapter.source_id
        self.product, self.api, self.page, self.facts, self.query = product, api, page, facts, query
        self.pid, self.vid = api.product_id, vid
        self.items = None if page.items is None else page.items.get(vid, [])
        self.option = api.options.get(vid)
        self.record = api.records.get(vid)
        self.evidence: dict[str, Evidence] = {}

    # Evidence and facts

    def cite(self, outcome: FetchOutcome, field_path: str, scope: str, *, pointer: str | None = None,
             text: str | None = None, tag: str = '') -> Evidence:
        return make_evidence(outcome, adapter_version=self.version, field_path=field_path, scope=scope,
                             product_id=None if scope == 'source' else self.pid,
                             variant_id=self.vid if scope == 'variant' else None,
                             json_pointer=pointer, text=text, tag=tag)

    def api_record(self, field_path: str, scope: str, pointer: str, tag: str) -> Evidence:
        return self.cite(self.api.outcome, field_path, scope, pointer=pointer, tag=tag)

    def name_record(self, field_path: str, scope: str) -> Evidence:
        return self.api_record(field_path, scope, json_pointer(self.api.index, 'name'), 'name')

    def page_record(self, field_path: str, scope: str, text: str, tag: str) -> Evidence:
        return self.cite(self.page.outcome, field_path, scope, text=text, tag=tag)

    def claim_record(self, claim: Claim, field_path: str, tag: str) -> Evidence:
        return self.page_record(field_path, claim.scope, claim.text, tag)

    def register(self, records) -> list[str]:
        ids = []
        for record in records:
            if self.evidence.setdefault(record.evidence_id, record) != record:
                raise ValueError('Two evidence records share an ID')
            if record.evidence_id not in ids:
                ids.append(record.evidence_id)
        return ids

    def known(self, value, records) -> dict:
        return {'state': 'known', 'value': value, 'evidence_ids': self.register(records)}

    def unknown(self, reason: str, records=()) -> dict:
        ids = self.register(records)
        return {'state': 'unknown', 'reason': reason, **({'evidence_ids': ids} if ids else {})}

    def combine(self, candidates, unknown: str, conflict: str) -> dict:
        """One value is known; distinct values are a conflict, citing every source."""
        values, records = [], []
        for value, cited in candidates:
            if value not in values:
                values.append(value)
            records += cited
        if not values:
            return self.unknown(unknown)
        if len(values) == 1:
            return self.known(values[0], records)
        return {'state': 'conflict', 'alternatives': values, 'reason': conflict,
                'evidence_ids': self.register(records)}

    # Variation options

    def options(self, names: frozenset[str], field_path: str):
        """This variation's option values (term names) for attributes with these role names.

        Returns the values with their evidence, whether a source leaves the option open (an empty
        value means 'any' in WooCommerce), and whether a value was empty after decoding.
        """
        found, open_, empty = [], False, False
        for attribute in self.api.attributes:
            if not attribute.variation or not attribute.roles & names:
                continue
            sources = []
            for item in self.items or ():
                raw = item.data['attributes'].get(attribute.key)
                if raw is not None:
                    sources.append((raw, self.page_record(
                        field_path, 'variant', f'variation {self.vid} {attribute.key} {raw}',
                        f'item{item.index}-attr{attribute.index}')))
            if self.option is not None and attribute.name in self.option[1]:
                entry, (position, raw) = self.option[0], self.option[1][attribute.name]
                pointer = json_pointer(self.api.index, 'variations', entry, 'attributes', position, 'value')
                record = self.api_record(field_path, 'variant', pointer, f'attr{attribute.index}')
                sources.append((raw, record))
            for raw, record in sources:
                if raw == '':
                    open_ = True
                elif not (value := attribute.term_name(raw)):
                    empty = True
                else:
                    found.append((value, [record]))
        return found, open_, empty

    # Facts

    def price(self) -> tuple[dict, str | None, int | None]:
        """The page price; the API record, when it is this variation, must agree with it."""
        if self.items is None:
            return self.unknown(self.page.reason), None, None
        if not self.items:
            return self.unknown(NOT_ON_PAGE), None, None
        minor, seen = self.api.minor_unit, []
        for item in self.items:
            raw = item.data.get('display_price')
            if isinstance(raw, bool) or not isinstance(raw, (int, Decimal)):
                return self.unknown(PRICE_NOT_NUMBER), None, None
            value = Decimal(raw)
            record = self.page_record('price', 'variant', f'variation {self.vid} display_price {raw}',
                                      f'item{item.index}')
            if not value.is_finite() or value < 0:
                return self.unknown(PRICE_INVALID), None, None
            if _places(value) > minor:
                return self.unknown(PRICE_DECIMALS, [record]), str(raw), _written_places(raw)
            try:
                amount = value.quantize(Decimal(1).scaleb(-minor))
            except InvalidOperation:
                return self.unknown(PRICE_INVALID), None, None
            seen.append((amount, raw, record))
        raw_price, digits = str(seen[0][1]), _written_places(seen[0][1])
        if self.api.currency is None:
            return self.unknown(NO_CURRENCY), raw_price, digits
        currency = self.api_record('price', 'variant',
                                   json_pointer(self.api.index, 'prices', 'currency_code'), 'currency')
        candidates = []
        if self.record is not None:
            position, record = self.record
            prices = record['prices']
            own = prices.get('currency_code')
            listed, cited = self.api.currency, currency
            if isinstance(own, str) and CURRENCY.fullmatch(own):
                listed = own
                cited = self.api_record('price', 'variant', json_pointer(position, 'prices', 'currency_code'),
                                        'api-currency')
            amount = minor_units(prices['price'], prices['currency_minor_unit'])
            stated = self.api_record('price', 'variant', json_pointer(position, 'prices', 'price'), 'api')
            candidates.append(({'amount': amount, 'currency': listed}, [stated, cited]))
            raw_price, digits = prices['price'], prices['currency_minor_unit']
        for amount, _, record in seen:
            candidates.append(({'amount': amount, 'currency': self.api.currency}, [record, currency]))
        fact = self.combine(candidates, NO_CURRENCY, PRICE_CONFLICT)
        if fact['state'] == 'known' and fact['value']['amount'] == 0:
            fact = {'state': 'unknown', 'reason': PRICE_ZERO, 'evidence_ids': fact['evidence_ids']}
        return fact, raw_price, digits

    def flags(self, field_path: str, keys: tuple[str, ...], unknown: str, conflict: str) -> dict:
        """A boolean fact from each page entry's flags: true only when every flag is true.

        The API record, when it is this variation, states the first flag too (the Store API has no
        variation_is_active); a different value is a conflict, as for the price.
        """
        if self.items is None:
            return self.unknown(self.page.reason)
        if not self.items:
            return self.unknown(NOT_ON_PAGE)
        found = []
        if self.record is not None:
            position, record = self.record
            stated = record.get(keys[0])
            if isinstance(stated, bool):
                found.append((stated, [self.api_record(field_path, 'variant', json_pointer(position, keys[0]),
                                                       'api')]))
        for item in self.items:
            values = [item.data.get(key) for key in keys]
            if not all(isinstance(v, bool) for v in values):
                return self.unknown(unknown)
            text = ' '.join(f'{key} {_json_bool(v)}' for key, v in zip(keys, values))
            record = self.page_record(field_path, 'variant', f'variation {self.vid} {text}',
                                      f'item{item.index}')
            found.append((all(values), [record]))
        return self.combine(found, unknown, conflict)

    def purchasable(self) -> dict:
        return self.flags('purchasable', ('is_purchasable', 'variation_is_active'), PURCHASABLE_UNKNOWN,
                          PURCHASABLE_CONFLICT)

    def online_stock(self) -> dict:
        """WooCommerce's is_in_stock is also true for a variation on backorder, so a backorder
        marker on a page entry or the API record keeps stock unknown; a backorder is never in stock."""
        fact = self.flags('online_stock', ('is_in_stock',), STOCK_UNKNOWN, STOCK_CONFLICT)
        if fact['state'] != 'known' or fact['value'] is not True:
            return fact
        records, stated = [], False
        for item in self.items:
            note = item.data.get('availability_html')
            note = note if isinstance(note, str) else ''
            if BACKORDER_CLASS.search(note):  # WooCommerce's own backorder class: the store says so
                text, tag = f'variation {self.vid} availability class available-on-backorder', \
                    f'item{item.index}-backorder'
                records.append(self.page_record('online_stock', 'variant', text, tag))
                stated = True
            elif item.data.get('backorders_allowed') is True and not IN_STOCK_CLASS.search(note):
                # Backorders allowed with no in-stock line is how a backorder without customer
                # notification shows, but a theme may also just hide the line: only a doubt.
                text, tag = (f'variation {self.vid} backorders_allowed true, no in-stock line',
                             f'item{item.index}-backorders-allowed')
                records.append(self.page_record('online_stock', 'variant', text, tag))
        if self.record is not None and self.record[1].get('is_on_backorder') is True:
            records.append(self.api_record('online_stock', 'variant',
                                           json_pointer(self.record[0], 'is_on_backorder'), 'api-backorder'))
            stated = True
        if not records:
            return fact
        return self.unknown(STOCK_BACKORDER if stated else STOCK_MAYBE_BACKORDER,
                            [self.evidence[i] for i in fact['evidence_ids']] + records)

    def _named_entries(self, key: str) -> list[tuple[int, str]]:
        entries = self.api.record.get(key)
        if not isinstance(entries, list):
            return []
        return [(k, _unescaped(e['name'])) for k, e in enumerate(entries)
                if isinstance(e, dict) and isinstance(e.get('name'), str)]

    def manufacturer(self) -> dict:
        """Brand attribute, brands, product name, then tags and categories; disagreement is a conflict."""
        field_path, found = 'variant.manufacturer', []
        for attribute in self.api.attributes:
            if attribute.roles & BRAND_NAMES and not attribute.variation and len(attribute.terms) == 1:
                maker = canonical_manufacturer(attribute.terms[0][1])
                if maker is not None:
                    found.append((maker, [self.api_record(field_path, 'product', json_pointer(
                        self.api.index, 'attributes', attribute.index, 'terms', 0, 'name'),
                        f'brand{attribute.index}')]))
        for k, name in self._named_entries('brands'):
            record = self.api_record(field_path, 'product', json_pointer(self.api.index, 'brands', k, 'name'),
                                     f'brands{k}')
            found += [(maker, [record]) for maker in sorted(find_manufacturers(name))]
        found += [(maker, [self.name_record(field_path, 'product')])
                  for maker in sorted(find_manufacturers(self.api.name))]
        for key in ('tags', 'categories'):
            for k, name in self._named_entries(key):
                record = self.api_record(field_path, 'product', json_pointer(self.api.index, key, k, 'name'),
                                         f'{key}{k}')
                found += [(maker, [record]) for maker in sorted(find_manufacturers(name))]
        return self.combine(found, MANUFACTURER_UNKNOWN, MANUFACTURER_CONFLICT)

    def model(self) -> dict:
        """The variation's model option and the product name's model; disagreement is a conflict."""
        field_path = 'variant.model'
        found, open_, empty = self.options(MODEL_NAMES, field_path)
        if open_:
            return self.unknown(MODEL_ANY)
        named = [(known_model(value), records) for value, records in found]  # exactly a model, no substring
        if empty or any(name is None for name, _ in named):
            return self.unknown(MODEL_UNREADABLE)
        name = match_model(self.api.name)
        if name is not None:
            named.append((name, [self.name_record(field_path, 'product')]))
        return self.combine(named, MODEL_UNKNOWN, MODEL_CONFLICT)

    def storage(self) -> dict:
        """The variation's storage option; otherwise the one size the product name gives."""
        field_path = 'variant.storage_gb'
        found, open_, empty = self.options(self.cfg.storage_names, field_path)
        if open_:
            return self.unknown(STORAGE_ANY)
        sizes = [(storage_size(value), records) for value, records in found]
        if empty or any(size is None for size, _ in sizes):
            return self.unknown(STORAGE_UNREADABLE)
        named = list(dict.fromkeys(storage_tokens(self.api.name)))
        if len(named) == 1:
            sizes.append((named[0], [self.name_record(field_path, 'variant')]))
        elif named and not sizes:
            return self.unknown(STORAGE_SEVERAL)
        elif named and any(size not in named for size, _ in sizes):
            record = self.name_record(field_path, 'variant')
            sizes += [(size, [record]) for size in named]
        return self.combine(sizes, STORAGE_UNKNOWN, STORAGE_CONFLICT)

    def color(self) -> dict:
        found, open_, empty = self.options(self.cfg.color_names, 'variant.color')
        if open_:
            return self.unknown(COLOR_ANY)
        if empty:
            return self.unknown(COLOR_UNREADABLE)
        return self.combine(found, COLOR_UNKNOWN, COLOR_CONFLICT)

    def sim(self) -> dict:
        """The store's table for the SIM option, or a SIM phrase in the product name."""
        field_path = 'variant.sim'
        found, open_, empty = self.options(self.cfg.sim_names, field_path)
        if open_:
            return self.unknown(SIM_ANY)
        if empty:
            return self.unknown(SIM_UNRECOGNIZED)
        stated, silent = [], []
        for value, records in found:
            key = normal(value)
            if key not in self.cfg.sim_map:
                return self.unknown(SIM_UNRECOGNIZED)
            if self.cfg.sim_map[key] is None:
                silent += records
            else:
                stated.append((self.cfg.sim_map[key], records))
        unconfirmed = []
        for text, sim in self.cfg.name_sim.items():
            if phrase(text).search(self.api.name):
                record = self.name_record(field_path, 'variant')
                if sim is None:
                    unconfirmed.append(record)
                else:
                    stated.append((sim, [record]))
        if stated:
            return self.combine(stated, SIM_UNKNOWN, SIM_CONFLICT)
        if unconfirmed:
            return self.unknown(SIM_NAME_UNCONFIRMED, unconfirmed + silent)
        return self.unknown(SIM_NOT_STATED, silent) if silent else self.unknown(SIM_UNKNOWN)

    def condition(self) -> dict:
        field_path = 'condition'
        found, open_, empty = self.options(CONDITION_NAMES, field_path)
        for attribute in self.api.attributes:  # a product-level statement covers every variation
            if attribute.roles & CONDITION_NAMES and not attribute.variation and len(attribute.terms) == 1:
                pointer = json_pointer(self.api.index, 'attributes', attribute.index, 'terms', 0, 'name')
                record = self.api_record(field_path, 'variant', pointer, f'cond{attribute.index}')
                found.append((attribute.terms[0][1], [record]))
        if open_:
            return self.unknown(CONDITION_ANY)
        values = [(normal(value), records) for value, records in found]
        if empty or any(value not in CONDITIONS for value, _ in values):
            return self.unknown(CONDITION_UNRECOGNIZED)
        return self.combine(values, CONDITION_UNKNOWN, CONDITION_CONFLICT)

    def warranty(self) -> dict:
        """Page, product-name and option statements; territory is never published."""
        providers, months = [], []

        def add(statement: WarrantyStatement | None, record_for):
            if statement is None:
                return
            provider, length = statement
            if provider:
                providers.append((provider, [record_for('warranty.provider')]))
            if type(length) is int and length >= 0:
                months.append((length, [record_for('warranty.months')]))

        if self.facts is not None and self.facts.warranty is not None:
            claim = self.facts.warranty
            add(claim.value, lambda path: self.claim_record(claim, path, 'warranty'))
        add(self.cfg.name_warranty(self.api.name), lambda path: self.name_record(path, 'product'))
        for path, bucket, part in (('warranty.provider', providers, 0), ('warranty.months', months, 1)):
            for value, records in self.options(self.cfg.warranty_names, path)[0]:
                statement = self.cfg.option_warranty(value)
                if statement is not None and statement[part] is not None:
                    bucket.append((statement[part], records))
        unread = self.facts is None
        return {
            'provider': self.combine(providers, PAGE_UNREAD if unread else WARRANTY_PROVIDER_UNKNOWN,
                                     WARRANTY_CONFLICT),
            'months': self.combine(months, PAGE_UNREAD if unread else WARRANTY_MONTHS_UNKNOWN,
                                   WARRANTY_CONFLICT),
            'territory': self.unknown(WARRANTY_TERRITORY_UNKNOWN),
        }

    def payment_basis(self) -> dict:
        if self.facts is None:
            return self.unknown(PAGE_UNREAD)
        claim = self.facts.payment_methods
        if claim is None:
            return self.unknown(PAYMENT_UNKNOWN)
        methods, record = set(claim.value), self.claim_record(claim, 'payment_basis', 'payment')
        if methods == {'cash', 'card'}:
            return self.unknown(CASH_AND_CARD, [record])
        if len(methods) == 1 and methods <= {'cash', 'card'}:
            return self.known(methods.pop(), [record])  # a store that takes one method prices in it
        return self.unknown(PAYMENT_UNKNOWN)

    def arrival(self) -> dict:
        if self.query.fulfillment.mode == 'pickup':
            return self.unknown(PICKUP_READINESS_UNKNOWN)
        if self.facts is None:
            return self.unknown(PAGE_UNREAD)
        claim = self.facts.arrival
        if claim is None:
            return self.unknown(DELIVERY_TIME_UNKNOWN)
        low, high, unit = claim.value
        try:
            window = arrival_window(self.page.outcome.observed_at, low, high, unit=unit, basis='estimate')
        except ValueError:
            return self.unknown(DELIVERY_TIME_UNKNOWN)
        return self.known(window, [self.claim_record(claim, 'arrival', 'arrival')])

    def tax(self, price: dict) -> dict:
        """Included only when the page's VAT statement follows this variation's own price."""
        if self.facts is None:
            return {'kind': 'tax', 'state': 'unknown', 'reason': PAGE_UNREAD}
        claims = self.facts.tax_included
        if not claims:
            return {'kind': 'tax', 'state': 'unknown', 'reason': VAT_UNKNOWN}
        amount = price['value']['amount'] if price['state'] == 'known' else None
        index = next((i for i, c in enumerate(claims) if amount is not None and c.value == amount), None)
        if index is None:
            return {'kind': 'tax', 'state': 'unknown', 'reason': VAT_UNBOUND}
        flags = vat_flags(self.page.json_ld, self.product.page_url)
        if False in flags:
            return {'kind': 'tax', 'state': 'unknown', 'reason': VAT_DISAGREE}
        records = [self.claim_record(claims[index], 'costs.tax', f'tax{index}')]
        if True in flags:
            records.append(self.page_record('costs.tax', 'product',
                                            'JSON-LD offer valueAddedTaxIncluded true', 'jsonld'))
        return {'kind': 'tax', 'state': 'included', 'evidence_ids': self.register(records)}

    def delivery_fee(self) -> dict:
        if self.facts is None:
            return {'kind': 'delivery', 'state': 'unknown', 'reason': PAGE_UNREAD}
        claim = self.facts.delivery_fee
        if claim is None:
            return {'kind': 'delivery', 'state': 'unknown', 'reason': DELIVERY_FEE_UNKNOWN}
        value = claim.value
        return {'kind': 'delivery', 'state': 'additional',
                'amount': {'amount': value['amount'], 'currency': value['currency']},
                'conditions': list(value['conditions']),
                'evidence_ids': self.register([self.claim_record(claim, 'costs.delivery', 'delivery')])}

    def costs(self, price: dict) -> list[dict]:
        # Explicit components, so the cost engine sees unknown charges instead of missing ones.
        costs = [self.tax(price)]
        if self.query.fulfillment.mode == 'delivery':
            costs.append(self.delivery_fee())
        return costs

    def pickup_stock(self) -> list[dict]:
        """The configured branches in the requested city (only the named one, when the request names a
        branch), each with unknown stock: online stock never proves a branch's stock. A branch is never
        made up, so a store without a configured branch there lists none."""
        route = self.query.fulfillment
        if route.mode != 'pickup':
            return []
        city = normal(route.city)
        stock = {'state': 'unknown', 'reason': BRANCH_STOCK_UNKNOWN}
        return [{'branch_id': branch, 'city': place, 'stock': stock} for branch, place in self.cfg.branches
                if normal(place) == city and route.branch_id in (None, branch)]

    def raw_attributes(self) -> dict:
        """Store values as written, bounded and JSON-safe; informative only."""
        item = self.items[0].data if self.items else None
        record = self.record[1] if self.record else None
        raw: dict[str, Any] = {'product_name': _clip(self.api.name)}
        sku = item.get('sku') if item is not None else record.get('sku') if record is not None else None
        if isinstance(sku, str):
            raw['sku'] = _clip(sku)
        if record is not None and isinstance(record.get('permalink'), str):
            raw['permalink'] = _clip(record['permalink'])
        if item is not None:
            raw['page_attributes'] = {_clip(k, 100): _clip(v)
                                      for k, v in list(item['attributes'].items())[:MAX_RAW_ITEMS]}
        written = {}
        for attribute in self.api.attributes[:MAX_RAW_ITEMS]:
            if not attribute.variation:
                written[_clip(attribute.name, 100)] = _clip(', '.join(name for _, name in attribute.terms))
                continue
            value = item['attributes'].get(attribute.key) if item is not None else None
            if value is None and self.option is not None and attribute.name in self.option[1]:
                value = self.option[1][attribute.name][1]
            if value is not None:
                written[_clip(attribute.name, 100)] = _clip(attribute.term_name(value))
        raw['attributes'] = written
        return raw

    def build(self) -> Offer:
        price, raw_price, digits = self.price()
        variant = {
            'source_id': self.source_id, 'product_id': self.pid, 'variant_id': self.vid,
            'manufacturer': self.manufacturer(), 'model': self.model(), 'storage_gb': self.storage(),
            'color': self.color(), 'sim': self.sim(), 'region': self.unknown(REGION_UNKNOWN),
            'raw_attributes': self.raw_attributes(),
        }
        data = {
            'offer_id': offer_id(self.source_id, self.pid, self.vid), 'source_id': self.source_id,
            'seller_id': self.source_id, 'variant': variant, 'price': price,
            'payment_basis': self.payment_basis(), 'raw_price': raw_price, 'price_fractional_digits': digits,
            'condition': self.condition(), 'purchasable': self.purchasable(),
            'online_stock': self.online_stock(), 'pickup_stock': self.pickup_stock(),
            'warranty': self.warranty(), 'fulfillment': self.query.fulfillment, 'arrival': self.arrival(),
            'costs': self.costs(price),
        }
        return Offer.model_validate({**data, 'evidence': list(self.evidence.values())})


@dataclass
class _Read:
    outcomes: list[FetchOutcome] = field(default_factory=list)
    errors: list[ToolError] = field(default_factory=list)
    offers: list[Offer] = field(default_factory=list)
    snapshots: list[Snapshot] = field(default_factory=list)
    variation_ids: list[str] = field(default_factory=list)
    listed: bool = False  # the product's variation list was read


def _excluded(product: ProductEndpoints, query: CandidateQuery) -> bool:
    wanted, listed = match_model(query.model), match_model(product.label)
    return wanted is not None and listed is not None and wanted != listed


class WooCommerceAdapter:
    """A StoreAdapter for one WooCommerce store; it fetches only its configured URLs."""

    def __init__(self, config: StoreConfig):
        self.config = config
        self.source_id = config.source_id
        self.adapter_version = config.adapter_version
        self._products = {p.key: p for p in config.products}
        self._policies = {p.policy_id: p for p in config.policies}

    @property
    def policy_topics(self) -> dict[str, tuple[str, ...]]:
        """Every path each page may settle, on any route."""
        return {p.policy_id: p.settles for p in self.config.policies}

    def policy_topics_for(self, mode: str) -> dict[str, tuple[str, ...]]:
        """The paths each page may settle for an offer on this route ('delivery' or 'pickup')."""
        return {p.policy_id: tuple(s for s in p.settles if mode == 'delivery' or s not in p.delivery_only)
                for p in self.config.policies}

    def search_candidates(self, fetch: Fetch, query: CandidateQuery) -> AdapterResult:
        """Every actual variation of each product the query's model does not rule out."""
        reads = [self._read(fetch, p, query) for p in self.config.products if not _excluded(p, query)]
        return self._result(reads)

    def fetch_offer(self, fetch: Fetch, offer_id: str, query: CandidateQuery) -> AdapterResult:
        """Re-read one offer's product and return only that variation."""
        try:
            source_id, product_id, variant_id = parse_offer_id(offer_id)
        except (ValueError, AttributeError, TypeError):
            source_id = product_id = variant_id = None
        product = self._products.get(product_id) if source_id == self.source_id else None
        if product is None:
            return self._result([], [self._error(UNKNOWN_OFFER)])
        read = self._read(fetch, product, query, only=variant_id)
        missing = read.listed and variant_id not in read.variation_ids
        return self._result([read], [self._error(OFFER_NOT_LISTED)] if missing else [])

    def fetch_policy(self, fetch: Fetch, policy_id: str) -> AdapterResult:
        """One policy page's statements; their evidence is made when apply_policy uses them.

        A configured page in which the reader recognizes no statement has probably changed its
        wording, so the read is partial with INVALID_SCHEMA; the document is still returned.
        """
        page = self._policies.get(policy_id) if isinstance(policy_id, str) else None
        data = {'policy_id': policy_id if isinstance(policy_id, str) else None, 'facts': 0}
        if page is None:
            return AdapterResult(tool_result([], [self._error(UNKNOWN_POLICY)], data, [], False))
        outcome = fetch(self.source_id, page.url, expect='html')
        if outcome.errors or outcome.body is None:
            return AdapterResult(tool_result([outcome], [], data, [], False))
        snapshot = Snapshot.of(outcome)
        try:
            text = visible_text(outcome.body)
        except AssertionError:
            return AdapterResult(tool_result([outcome], [self._error(POLICY_MARKUP)], data, [], False),
                                 snapshots=(snapshot,))
        facts = tuple(page.extract(text))
        document = PolicyDocument(
            source_id=self.source_id, policy_id=page.policy_id, url=outcome.origin_url,
            final_url=outcome.final_url, observed_at=outcome.observed_at,
            response_sha256=outcome.response_sha256, adapter_version=self.adapter_version, facts=facts)
        errors = [] if facts else [self._error(POLICY_NO_STATEMENTS)]
        return AdapterResult(tool_result([outcome], errors, {**data, 'facts': len(facts)}, [], True),
                             policies=(document,), snapshots=(snapshot,))

    def apply_policy(self, offer: Offer, policy: PolicyDocument) -> Offer:
        """A new offer with the page's statements merged; existing facts stay, disagreement conflicts."""
        if offer.source_id != self.source_id or policy.source_id != self.source_id:
            raise ValueError('A policy applies only to offers from its own source')
        if policy.policy_id not in self._policies:
            raise ValueError('Unknown policy for this source')
        return self.config.policy_effect(offer, policy)

    def _error(self, reason: str) -> ToolError:
        return ToolError(code='INVALID_SCHEMA', message=f'{self.source_id}: {reason}', retryable=False)

    def _read(self, fetch: Fetch, product: ProductEndpoints, query: CandidateQuery,
              only: str | None = None) -> _Read:
        """API then page: two requests. Without a usable API reply the page is not fetched."""
        read = _Read()
        api_outcome = fetch(self.source_id, product.api_url, expect='json')
        read.outcomes.append(api_outcome)
        if api_outcome.errors or api_outcome.body is None:
            return read
        read.snapshots.append(Snapshot.of(api_outcome))
        try:
            api = parse_api(api_outcome, product.key)
        except _SchemaError as exc:
            read.errors.append(self._error(exc.reason))
            return read
        page_outcome = fetch(self.source_id, product.page_url, expect='html')
        read.outcomes.append(page_outcome)
        if not page_outcome.errors and page_outcome.body is not None:
            read.snapshots.append(Snapshot.of(page_outcome))
        page, problem = read_page(page_outcome, api.product_id)
        if problem is not None:
            read.errors.append(self._error(problem))
        facts = None if page.text is None else self.config.page_facts(page.text)
        ids = list(api.options)
        ids += [vid for vid in (page.items or {}) if vid not in api.options]
        read.listed, read.variation_ids = True, ids
        for vid in ids:
            if only is not None and vid != only:
                continue
            try:
                read.offers.append(_OfferBuilder(self, product, api, page, facts, query, vid).build())
            except ValidationError:
                read.errors.append(self._error(OFFER_UNREADABLE))
        return read

    def _result(self, reads: list[_Read], errors=()) -> AdapterResult:
        outcomes = [o for r in reads for o in r.outcomes]
        offers = [o for r in reads for o in r.offers]
        data = {'offer_ids': [o.offer_id for o in offers],
                'variants_seen': sum(len(r.variation_ids) for r in reads),
                'variants_without_price': sum(o.price.state != 'known' for o in offers)}
        evidence_ids = [e.evidence_id for o in offers for e in o.evidence]
        errors = [e for r in reads for e in r.errors] + list(errors)
        result = tool_result(outcomes, errors, data, evidence_ids, bool(offers))
        snapshots = tuple(s for r in reads for s in r.snapshots)
        return AdapterResult(result, offers=tuple(offers), snapshots=snapshots)
