"""Reviewed Shopify evidence for Lebanese sellers whose native variant IDs are on their offers."""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from html.parser import HTMLParser
import json
import re
from urllib.parse import urlsplit

from app.catalog_extraction import offer_seller_matches
from app.contracts_v2 import Evidence
from app.corpus_contracts import Extraction, FieldObservation, PassageDraft
from app.extraction import _excerpt, _unique_pairs


HOSTS = {'outgeeked': 'outgeeked.net', 'wincell': 'wincell.store'}
COLLECTION = 'https://wincell.store/collections/vendors?q=Soundcore'
COLLECTION_DISPATCH = 'Every Soundcore piece on our shelves in Kaslik. Delivered across Lebanon, cash at your door.'
BEIRUT_DISPATCH = 'Ships from Beirut, Lebanon'


@dataclass
class Card:
    links: dict = field(default_factory=dict)
    inputs: dict = field(default_factory=dict)


class StorePage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.structured = []
        self.visible = set()
        self.cards = []
        self.card = self.script = None
        self.ignored = 0
        self.forms = []
        self.form = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'style', 'template', 'noscript', 'head'):
            self.ignored += 1
        if tag == 'script' and attrs.get('type') == 'application/ld+json':
            self.script = []
        if self.ignored:
            return
        if tag == 'article' and 'card' in attrs.get('class', '').split():
            self.card = Card()
        if tag == 'form':
            self.form = {}
        if tag == 'a' and self.card and attrs.get('href'):
            self.card.links[attrs['href']] = self.get_starttag_text()
        if tag == 'input' and attrs.get('name') in ('id', 'product-id') and self.form is not None:
            self.form.setdefault(attrs['name'], []).append((attrs.get('value'), self.get_starttag_text()))

    def handle_endtag(self, tag):
        if tag == 'script' and self.script is not None:
            self.structured.append(''.join(self.script))
            self.script = None
        if tag in ('script', 'style', 'template', 'noscript', 'head'):
            self.ignored = max(0, self.ignored - 1)
        if not self.ignored and tag == 'form' and self.form is not None:
            self.forms.append(self.form)
            if self.card is not None:
                self.card.inputs = self.form
            self.form = None
        if not self.ignored and tag == 'article' and self.card is not None:
            self.cards.append(self.card)
            self.card = None

    def handle_data(self, value):
        if self.script is not None:
            self.script.append(value)
        elif not self.ignored and value.strip():
            self.visible.add(value.strip())


def _page(capture, listing, url, run):
    if (listing.source_id not in HOSTS or listing.source_id != listing.retailer_id
            or listing.source_id != listing.seller_id or capture.visibility != 'public'
            or (run is not None and capture.origin != 'runtime')
            or capture.key.source_id != listing.source_id or capture.key.product_id != listing.product_id
            or capture.key.variant_id != listing.sku or not listing.sku.isdecimal()
            or str(capture.final_url) != url or str(capture.key.url) != url
            or urlsplit(url).netloc != HOSTS[listing.source_id]):
        return None
    page = StorePage()
    page.feed(capture.body.decode('utf-8'))
    page.close()
    return page


def _observation(capture, run, field_name, value, citations):
    body_hash = sha256(capture.body).hexdigest()
    key = capture.key
    route = field_name.startswith('route.')
    receipts = []
    for pointer, excerpt in citations:
        identity = (key.model_dump(mode='json'), body_hash, capture.observed_at.isoformat(), field_name,
                    pointer, capture.processing.model_dump(), run.model_dump() if run else None)
        receipts.append(Evidence(
            evidence_id=sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest(),
            source_id=key.source_id, product_id=key.product_id, variant_id=key.variant_id,
            scope='route' if route else 'variant', route_id=(key.route_id or 'catalog-dispatch') if route else None,
            field_path=field_name, origin_url=key.url, final_url=capture.final_url, observed_at=capture.observed_at,
            response_sha256=body_hash, adapter_version=capture.processing.extraction_version,
            json_pointer=pointer, excerpt=excerpt, origin=capture.origin, corpus_version=capture.processing.corpus_version,
            index_version=capture.processing.index_version, run=run))
    return FieldObservation(fact=dict(state='known', value=value, evidence_ids=[r.evidence_id for r in receipts]),
                            evidence=tuple(receipts))


def _matches(inputs, listing):
    return all([v for v, _ in inputs.get(key, [])] == [expected]
               for key, expected in (('id', listing.sku), ('product-id', listing.product_id)))


def extract_dispatch_membership(capture, listing, *, run=None):
    """Bind WinCell's explicit dispatch statement to a product and SKU actually present in that collection."""
    if listing.source_id != 'wincell':
        return Extraction()
    page = _page(capture, listing, COLLECTION, run)
    if page is None or COLLECTION_DISPATCH not in page.visible:
        return Extraction()
    subject = urlsplit(str(listing.listing_url))
    if subject.netloc != HOSTS['wincell'] or subject.query not in ('', 'variant=' + listing.sku):
        return Extraction()
    cards = [card for card in page.cards if subject.path in card.links and _matches(card.inputs, listing)]
    if len(cards) != 1:
        return Extraction()
    card = cards[0]
    citations = [
        ('/collection/dispatch/statement', COLLECTION_DISPATCH),
        ('/collection/dispatch/listing', f'href="{subject.path}"'),
        ('/collection/dispatch/variant', card.inputs['id'][0][1]),
        ('/collection/dispatch/product', card.inputs['product-id'][0][1]),
    ]
    if citations[1][1] not in card.links[subject.path]:
        return Extraction()
    return Extraction(observations=(_observation(capture, run, 'route.origin_country', 'LB', citations),))


def _selected(page, listing):
    """A single product or group member must explicitly publish this variant's URL and price."""
    target = urlsplit(str(listing.listing_url))
    base = f'{target.scheme}://{target.netloc}{target.path}'
    selected_url = base + '?variant=' + listing.sku
    if not target.path.startswith('/products/') or target.query not in ('', 'variant=' + listing.sku):
        return
    for i, script in enumerate(page.structured):
        try:
            root = json.loads(script, parse_float=Decimal, object_pairs_hook=_unique_pairs)
        except (ValueError, RecursionError):
            continue
        if not isinstance(root, dict) or root.get('url') != base:
            continue
        path = f'/jsonld/{i}'
        if root.get('@type') == 'ProductGroup' and str(root.get('productGroupID')) == listing.product_id:
            members = root.get('hasVariant')
            if not isinstance(members, list):
                continue
            entries = [(product, path + f'/hasVariant/{j}') for j, product in enumerate(members)]
        elif root.get('@type') == 'Product' and any(_matches(form, listing) for form in page.forms):
            entries = [(root, path)]
        else:
            continue
        for product, pointer in entries:
            if not isinstance(product, dict) or product.get('@type') != 'Product':
                continue
            offer = product.get('offers')
            if isinstance(offer, dict) and offer.get('@type') == 'Offer' and offer.get('url') == selected_url:
                yield root, product, offer, script, pointer


def enrich_listing(capture, listing, extraction, *, run=None, destination=None):
    if listing.source_id not in HOSTS:
        return extraction
    page = _page(capture, listing, str(listing.listing_url), run)
    selected = list(_selected(page, listing)) if page else []
    # The generic Shopify "mpn" frequently repeats a retailer's internal SKU.
    # Read these two storefronts only through the selected native variant.
    if len(selected) != 1:
        return Extraction()
    root, product, offer, script, prefix = selected[0]
    if not offer_seller_matches(offer, (listing.seller_name, *listing.seller_aliases)):
        return Extraction()
    observations, passages = [], []

    def add(name, value, citations, text=None):
        item = _observation(capture, run, name, value, citations)
        observations.append(item)
        if text:
            passages.append(PassageDraft(locator=citations[0][0], text=text, evidence=item.evidence[0]))

    def cite(path, key, value):
        return path + '/' + key, _excerpt(script, key, value)

    price, currency = offer.get('price'), offer.get('priceCurrency')
    if currency == 'USD' and isinstance(price, (str, int, Decimal)) and not isinstance(price, bool):
        try:
            amount = Decimal(price)
            if amount.is_finite() and amount > 0:
                add('price', dict(amount=str(amount), currency=currency),
                    [cite(prefix + '/offers', 'price', price), cite(prefix + '/offers', 'priceCurrency', currency)])
        except InvalidOperation:
            pass
    status = offer.get('availability')
    stocks = {protocol + '://schema.org/' + key: val for protocol in ('http', 'https')
              for key, val in [('InStock', True), ('OutOfStock', False)]}
    if isinstance(status, str) and status in stocks:
        add('stock', stocks[status], [cite(prefix + '/offers', 'availability', status)])
    name = product.get('name')
    brand = root.get('brand')
    maker = brand.get('name') if isinstance(brand, dict) else None
    # This reviewed audio configuration is defined by its commercial model and colour.
    # Never treat similar models, bundles or unspecified/multiple colours as one variant.
    recognized = {
        'outgeeked': ('Anker', r'Soundcore H30i - (Black|White|Red)'),
        'wincell': ('Soundcore', r'Anker Soundcore H30i Wireless On-Ear Headphones [–-] (Black|White|Red)'),
    }
    expected_brand, pattern = recognized[listing.source_id]
    match = re.fullmatch(pattern, name or '') if listing.category == 'audio' and maker == expected_brand else None
    if match:
        color = match.group(1)
        title = [cite(prefix, 'name', name)]
        brand_path = prefix.split('/hasVariant/')[0] + '/brand'
        brand_cite = [cite(brand_path, 'name', maker)] if maker == 'Anker' else title
        add('variant.product.manufacturer', 'Anker', brand_cite)
        add('variant.product.model', 'Soundcore H30i', title, text=name)
        add('variant.color', color, title)
        add('catalog.configuration_id', 'soundcore:h30i:' + color.lower(), title)
    if listing.source_id == 'outgeeked' and BEIRUT_DISPATCH in page.visible:
        add('route.origin_country', 'LB', [('/store/dispatch/statement', BEIRUT_DISPATCH)])
    return Extraction(observations=tuple(observations), passages=tuple(passages))
