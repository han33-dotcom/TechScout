"""Reviewed pre-order delivery quote routes (#186): the one narrow exception to the read-only gate.

Every retailer read in app.sources is a GET, and the gate refuses cart, checkout and account
paths. A source may also declare `quote_capabilities`. Each one names a protocol that is
defined here, in code. The registry supplies only typed settings: host, API version, currency,
quantity cap, how precise a destination the retailer needs, and an optional token variable.
It never supplies a URL template, a request body or a header.

The only protocol is Shopify's Storefront API `cartCreate`. One POST builds a new anonymous cart
holding exactly one variant and quantity for the buyer's destination. The response contains the
line's price and the cart's delivery options. The request:

- asks for no checkout URL;
- creates no order, payment, reservation, account or message;
- sends no cookie;
- holds no stock, because Shopify carts do not reserve it.

The gate rebuilds the expected request from these rules and refuses any other method, path,
query, document, variable or address field.

A route is declared with status 'pending_live_check'. The gate refuses it until a teammate
checks it against the live store and records status 'active'.
"""
import json
import os
import re
from typing import Annotated, Literal

from pydantic import Field, StrictInt, StringConstraints, model_validator

from app.source_capabilities import ReferenceText, SafeHost, _named_host, _ScopeModel, safe_endpoint

QUOTE_PROTOCOLS = ('shopify_storefront_cart',)
MAX_QUOTE_BODY_BYTES = 4096
MAX_QUOTE_RESPONSE_BYTES = 256 * 1024
MAX_QUOTE_QUANTITY = 5
COUNTRY_NAME = 'Lebanon'
COUNTRY_CODE = 'LB'
# The whole Storefront document, fixed. It requests no checkoutUrl, no buyer or address echo and
# no other mutation; app.delivery_quotes reads exactly these fields.
STOREFRONT_DOCUMENT = (
    'mutation TechScoutDeliveryQuote($input: CartInput!) { cartCreate(input: $input) { cart { '
    'lines(first: 2) { nodes { quantity merchandise { ... on ProductVariant { id availableForSale } } '
    'cost { totalAmount { amount currencyCode } } } } '
    'deliveryGroups(first: 2) { nodes { deliveryOptions { handle title '
    'estimatedCost { amount currencyCode } } } } } '
    'userErrors { code field } warnings { code } } }'
)
VARIANT_GID = re.compile(r'gid://shopify/ProductVariant/[1-9][0-9]{0,19}')
TOKEN = re.compile(r'[A-Za-z0-9_-]{16,128}')
# Plain text only: no control, bidirectional or angle-bracket characters reach a retailer.
_UNSAFE = re.compile(r'[\x00-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩<>]')
_ADDRESS_LIMITS = {'city': 80, 'address1': 200, 'address2': 200}

TokenVariable = Annotated[str, StringConstraints(strict=True, pattern=r'^TECHSCOUT_[A-Z0-9_]{1,64}$')]
ApiVersion = Annotated[str, StringConstraints(strict=True, pattern=r'^20[0-9]{2}-(01|04|07|10)$')]
EvidenceUrl = Annotated[str, StringConstraints(strict=True, min_length=9, max_length=2048, pattern=r'^https://')]


class QuoteReview(_ScopeModel):
    """Who checked the route, how, and when. A live check is required before status 'active'."""
    basis: ReferenceText
    evidence: Annotated[tuple[EvidenceUrl, ...], Field(min_length=1, max_length=8)]
    reviewed_at: ReferenceText


class QuoteCapability(_ScopeModel):
    capability_id: ReferenceText
    version: ReferenceText
    host: SafeHost
    protocol: Literal['shopify_storefront_cart']
    api_version: ApiVersion
    status: Literal['pending_live_check', 'active', 'disabled']
    # 'city': the retailer's rate depends on the city at most, so TechScout quotes the search's
    # own city without asking. 'street': the buyer's selected address is needed, and only a
    # seller the buyer named for that chat receives it.
    destination_detail: Literal['city', 'street']
    currency: Annotated[str, StringConstraints(strict=True, pattern=r'^[A-Z]{3}$')]
    max_quantity: Annotated[StrictInt, Field(ge=1, le=MAX_QUOTE_QUANTITY)] = 1
    token_env: TokenVariable | None = None
    review: QuoteReview

    @model_validator(mode='after')
    def safe_route(self):
        if not _named_host(self.host) or not safe_endpoint(quote_url(self), self.host):
            raise ValueError('A quote route needs a named host and a read-safe path')
        return self


def quote_url(rule: QuoteCapability) -> str:
    return f'https://{rule.host}/api/{rule.api_version}/graphql.json'


def validate_quote_scope(rules: tuple[QuoteCapability, ...], host: str) -> None:
    ids = set()
    for rule in rules:
        if rule.host != host or rule.capability_id in ids:
            raise ValueError('Quote routes must be unique per source and belong to its configured host')
        ids.add(rule.capability_id)


def _plain(value, limit):
    if not isinstance(value, str):
        return None
    text = ' '.join(value.split())
    return text if 1 <= len(text) <= limit and not _UNSAFE.search(text) else None


def storefront_body(rule: QuoteCapability, variant_id: str, quantity: int, *, city: str,
                    street: str | None = None, details: str | None = None) -> bytes:
    """The one request body this protocol sends. ValueError when any input is out of scope."""
    if not re.fullmatch(r'[1-9][0-9]{0,19}', variant_id or ''):
        raise ValueError('The listing has no Shopify variant ID')
    if type(quantity) is not int or not 1 <= quantity <= rule.max_quantity:
        raise ValueError('Quantity is outside the reviewed quote route')
    address = {'country': COUNTRY_NAME, 'city': _plain(city, _ADDRESS_LIMITS['city'])}
    if rule.destination_detail == 'street':
        address['address1'] = _plain(street, _ADDRESS_LIMITS['address1'])
        if details is not None:
            address['address2'] = _plain(details, _ADDRESS_LIMITS['address2'])
    elif street is not None or details is not None:
        raise ValueError('A city-level route never receives a street address')
    if any(value is None for value in address.values()):
        raise ValueError('The destination is not plain text within the route limits')
    variables = {'input': {
        'lines': [{'merchandiseId': f'gid://shopify/ProductVariant/{variant_id}', 'quantity': quantity}],
        'buyerIdentity': {'countryCode': COUNTRY_CODE,
                          'deliveryAddressPreferences': [{'deliveryAddress': address}]},
    }}
    body = json.dumps({'query': STOREFRONT_DOCUMENT, 'variables': variables},
                      separators=(',', ':'), ensure_ascii=True).encode('ascii')
    if len(body) > MAX_QUOTE_BODY_BYTES:
        raise ValueError('The quote request is too large')
    return body


def storefront_request_allowed(rule: QuoteCapability, url: str, body: bytes) -> bool:
    """Independent send-time check: exactly the reviewed document, one line, one destination."""
    if type(url) is not str or type(body) is not bytes or url != quote_url(rule):
        return False
    if not 0 < len(body) <= MAX_QUOTE_BODY_BYTES:
        return False
    try:
        data = json.loads(body.decode('ascii'), object_pairs_hook=_no_duplicates)
        lines = data['variables']['input']['lines']
        identity = data['variables']['input']['buyerIdentity']
        line, = lines
        preference, = identity['deliveryAddressPreferences']
        address = preference['deliveryAddress']
    except (UnicodeDecodeError, ValueError, KeyError, TypeError):
        return False
    fields = {'country', 'city'} | ({'address1'} if rule.destination_detail == 'street' else set())
    optional = {'address2'} if rule.destination_detail == 'street' else set()
    return (set(data) == {'query', 'variables'} and data['query'] == STOREFRONT_DOCUMENT
            and set(data['variables']) == {'input'} and set(data['variables']['input']) == {'lines', 'buyerIdentity'}
            and set(line) == {'merchandiseId', 'quantity'} and isinstance(line['merchandiseId'], str)
            and VARIANT_GID.fullmatch(line['merchandiseId']) is not None
            and type(line['quantity']) is int and 1 <= line['quantity'] <= rule.max_quantity
            and set(identity) == {'countryCode', 'deliveryAddressPreferences'}
            and identity['countryCode'] == COUNTRY_CODE and set(preference) == {'deliveryAddress'}
            and fields <= set(address) <= fields | optional and address['country'] == COUNTRY_NAME
            and all(_plain(address[key], _ADDRESS_LIMITS[key]) == address[key] for key in address if key != 'country'))


def _no_duplicates(pairs):
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError('Duplicate JSON keys')
    return dict(pairs)


def storefront_token(rule: QuoteCapability) -> str | None:
    """The public Storefront token from the backend environment; '' when one is required and absent."""
    if rule.token_env is None:
        return None
    value = os.environ.get(rule.token_env, '').strip()
    return value if TOKEN.fullmatch(value) else ''


def active_quote_routes(registry) -> dict[str, QuoteCapability]:
    """source ID -> its active quote route, for admitted sources only. Read fresh on every call."""
    from app.contracts import SourcePolicy
    from app.source_registry import get_active_source, load_registry
    try:
        document = load_registry(registry)
        admitted = document['live_release']['admitted_source_ids']
        ids = [entry.get('id') for entry in document['sources'] if isinstance(entry, dict)]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}
    routes = {}
    for source_id in ids:
        if not isinstance(source_id, str) or not isinstance(admitted, list) or source_id not in admitted:
            continue
        try:
            policy = SourcePolicy.model_validate(get_active_source(source_id, registry))
        except (PermissionError, ValueError):
            continue
        active = [rule for rule in policy.quote_capabilities if rule.status == 'active']
        if len(active) == 1:
            routes[source_id] = active[0]
    return routes
