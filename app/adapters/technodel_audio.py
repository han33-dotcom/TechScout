"""Read Technodel's public headset selection, item offer and published COD charges."""
from decimal import Decimal
from html.parser import HTMLParser
import json
import re
from urllib.parse import urlsplit

from app import contracts_v2 as v2
from app.adapters.itworks_audio import nodes, policy_answers, single
from app.extraction import _unique_pairs
from app.headset_identity import identify_headset


SOURCE = 'technodel'
ORIGIN = 'https://technodel.net'
FAQ = ORIGIN + '/faq'
SELLER = 'Technodel'
PRODUCT_PATH = re.compile(r'/product/[a-z0-9][a-z0-9-]{0,127}')
PART_SUFFIX = re.compile(r'-\d{3}-\d{6}$')
WIRED_TITLE = re.compile(r'(?P<maker>[A-Za-z]+) Wired (?P<model>[A-Z]+[0-9]+[A-Za-z]*) Headset'
                        r'(?: with Noise-Canceling Mic)? \((?P<color>[A-Za-z]+)\) (?P<part>\d{3}-\d{6})', re.I)


class Page(HTMLParser):
    def __init__(self, body):
        super().__init__()
        self.links = []
        self.products = []
        self.script = False
        self.feed(body.decode('utf-8'))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and PRODUCT_PATH.fullmatch(attrs.get('href') or ''):
            self.links.append(attrs['href'])
        if tag == 'script':
            self.script = True

    def handle_endtag(self, tag):
        if tag == 'script':
            self.script = False

    def handle_data(self, data):
        # The public purchase component contains the selected product and its variants.
        # Read only its serialized JSON. Never execute retailer JavaScript.
        if not self.script or not data.startswith('self.__next_f.push('):
            return
        try:
            frame = json.loads(data.removeprefix('self.__next_f.push(').removesuffix(')'),
                               object_pairs_hook=_unique_pairs)
            if not isinstance(frame, list) or len(frame) != 2 or frame[0] != 1:
                return
            for line in frame[1].splitlines():
                _, _, payload = line.partition(':')
                if not payload.startswith('["$"'):
                    continue
                component = json.loads(payload, parse_float=Decimal, object_pairs_hook=_unique_pairs)
                if (len(component) == 4 and isinstance(component[3], dict)
                        and isinstance(component[3].get('product'), dict)):
                    self.products.append(component[3]['product'])
        except (AttributeError, TypeError, ValueError):
            return


def product_links(body, model):
    tokens = set(re.findall(r'[a-z0-9]+', model.lower()))
    if not tokens:
        return ()
    matches = [path for path in Page(body).links
               if tokens <= set(path.split('/')[-1].split('-')) and PART_SUFFIX.search(path)]
    return tuple(ORIGIN + path for path in dict.fromkeys(matches))[:1]


def read_offer(page, policy, request, owner, run, route_id):
    url = page.origin_url
    if (page.final_url != url or not url.startswith(ORIGIN + '/product/')
            or owner.entry.name != SELLER or request.fulfillment.destination.country != 'LB'):
        return None
    listing, = Page(page.body).products
    data = nodes(page)
    product = single(data, 'Product')
    store, = [node for node in data if isinstance(node, dict) and isinstance(node.get('@type'), list)
              and 'ElectronicsStore' in node['@type']]
    offer = product['offers']
    sku = listing['sku']
    title = listing['title']
    match = WIRED_TITLE.fullmatch(title)
    if (not match or listing['slug'] != urlsplit(url).path.removeprefix('/product/')
            or listing['variants'] != [] or listing['category']['name'] != 'Headsets'
            or product.get('category') != 'Headsets' or listing['isVisible'] is not True
            or listing['priceDisplay'] != 'price' or listing['inStock'] is not True
            or type(listing['stock']) is not int or listing['stock'] < 1 or not sku
            or (product['sku'], product['name'], product['url'], offer['url']) != (sku, title, url, url)
            or offer['availability'] != 'https://schema.org/InStock'
            or offer['itemCondition'] != 'https://schema.org/NewCondition'
            or offer['seller'].get('name') != SELLER or offer['seller'].get('url') != ORIGIN
            or (store['name'], store['url']) != (SELLER, ORIGIN)
            or store['address']['addressCountry'] != 'LB' or not store['address'].get('addressLocality')):
        return None
    maker, model, color, part = match.group('maker', 'model', 'color', 'part')
    specs = _unique_pairs((s['label'], s['value']) for s in listing['specs'])
    if (product['brand']['name'] != maker or listing['brand'] != maker or specs.get('Brand') != maker
            or specs.get('Connectivity') != 'Wired' or specs.get('Colour') != color):
        return None
    amount = Decimal(str(offer['price']))
    # The current USD checkout renders whole dollars. Fractional listings need a distinct quote.
    if (not amount.is_finite() or amount <= 0 or amount != amount.to_integral_value()
            or amount != Decimal(str(listing['displayPrice'])) or offer['priceCurrency'] != 'USD'
            or listing['currency'] != 'USD' or 'priceSpecification' in offer):
        return None
    configuration = identify_headset(maker, model, color, part=part)
    pid = listing['slug']
    source_key = f'{SOURCE}:{sku}'
    evidence = []

    def receipt(path, excerpt, *, document=page, route=False):
        eid = f'{source_key}:{path}:{len(evidence)}'
        evidence.append(v2.Evidence(evidence_id=eid, field_path=path, source_id=SOURCE, product_id=pid,
            variant_id=sku, scope='route' if route else 'variant', route_id=route_id if route else None,
            origin_url=document.origin_url, final_url=document.final_url, observed_at=document.observed_at,
            response_sha256=document.response_sha256, adapter_version='technodel-audio/1',
            origin='runtime', corpus_version='domestic-buyer-1', run=run, excerpt=excerpt))
        return [eid]

    def known(path, value, excerpt, **kwargs):
        return dict(state='known', value=value, evidence_ids=receipt(path, excerpt, **kwargs))

    receipt('seller.name', SELLER)
    receipt('seller.market', f"{SELLER}: {store['address']['addressLocality']}, LB")
    receipt('variant.product.manufacturer', maker)
    receipt('variant.product.model', title)
    receipt('catalog.mpn', part)
    if configuration:
        receipt('catalog.configuration_id', title)
    answers = policy_answers(policy, FAQ)
    details = offer.get('shippingDetails', {})
    served = (details.get('@type') == 'OfferShippingDetails'
              and set(details) <= {'@type','shippingRate','shippingDestination','deliveryTime'}
              and details.get('shippingDestination') == {'@type':'DefinedRegion','addressCountry':'LB'}
              and answers.get('How long does shipping take within Lebanon?', '') ==
                  ('We deliver to all Lebanese regions. Same-day delivery to Beirut, Sour, Tripoli and surroundings. '
                   'Every other region: 1–2 days, never more.'))
    service = (known('route.item_eligible', True, 'Selected Offer shippingDestination: LB', route=True)
               if served else dict(state='unknown', reason='Nationwide service unconfirmed'))
    shipping = dict(kind='shipping', state='unknown', reason='No matching current item and nationwide rate')
    terms = answers.get('What are the shipping costs?', '')
    rate = re.fullmatch(r'Delivery is a flat \$(\d+(?:\.\d{1,2})?) anywhere in Lebanon — '
                        r'Beirut, South, North, Bekaa and Mount Lebanon alike — and free on orders of '
                        r'\$(\d+(?:\.\d{1,2})?) or more\.', terms)
    fee = details.get('shippingRate', {})
    if served and rate and set(fee) == {'@type','currency','value'} and fee['@type'] == 'MonetaryAmount':
        expected = Decimal(rate[1]) if amount < Decimal(rate[2]) else Decimal(0)
        if fee['currency'] == 'USD' and Decimal(str(fee['value'])) == expected:
            shipping = dict(kind='shipping', state='additional', amount=dict(amount=expected, currency='USD'),
                evidence_ids=receipt('costs.order.shipping', str(fee), route=True)
                           + receipt('costs.order.shipping', terms, document=policy, route=True))
    payment = answers.get('What payment methods do you accept?', '')
    tax = dict(kind='tax', state='unknown', reason='Additional charges at payment unconfirmed')
    sale_text = ' '.join(str(listing.get(field, '')) for field in ('title','shortDescription','description','specs'))
    tax_extra = re.search(r'(?:exclud(?:es|ing)|before|additional|plus)\s+(?:VAT|tax)|'
                          r'(?:VAT|tax)(?:es)?\s+(?:excluded|extra|calculated|added)', sale_text, re.I)
    if not tax_extra and payment == ('We accept Cash on Delivery (COD), Wish Money transfer, and Crypto (USDT, BTC, ETH). '
                   'No hidden fees — what you see is what you pay.'):
        tax = dict(kind='tax', state='included', evidence_ids=receipt('costs.order.tax',
            f'Displayed COD item price: USD {amount}') + receipt('costs.order.tax', payment, document=policy))
    return v2.Offer(offer_id=source_key, retailer_id=owner.entry.retailer_id, source_id=SOURCE,
        source_product_id=pid, source_variant_id=sku, seller=dict(seller_id=owner.seller_id, name=SELLER, market='LB'),
        listing_url=url, variant=dict(variant_id=configuration.variant_id if configuration else source_key,
            product=dict(product_id=configuration.product_id if configuration else source_key,
                         category='audio', manufacturer=maker, model=maker + ' ' + model),
            color=known('variant.color', color, title), specs=dict(category='audio',
                form_factor=known('variant.specs.form_factor','headphones', title),
                connectivity=known('variant.specs.connectivity','wired','Connectivity: Wired'))),
        price=known('price',dict(amount=amount,currency='USD'),f'Offer and displayed price: USD {amount}'),
        stock=known('stock',True,offer['availability']), purchasable=known('purchasable',True,
            f'Visible priced purchase selection {sku}, inStock true, stock {listing["stock"]}'),
        condition=known('condition','new',offer['itemCondition']),
        route=dict(kind='direct', route_id=route_id, origin_country='ZZ', destination=request.fulfillment.destination,
                   item_eligible=service), costs=(tax, shipping), evidence=tuple(evidence))
