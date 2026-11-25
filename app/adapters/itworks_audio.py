"""Read ITWorks' single-selection headphone pages, their purchase forms and published terms."""
from decimal import Decimal
from html.parser import HTMLParser
import json
import re
from urllib.parse import urlsplit

from app import contracts_v2 as v2
from app.headset_identity import identify_headset
from app.extraction import _HTML, _unique_pairs


SOURCE = 'itworks'
ORIGIN = 'https://shop.itworkslb.net'
FAQ = ORIGIN + '/faq'
SELLER = 'ITWorks Lebanon'
PRODUCT_PATH = re.compile(r'/products/[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}')
# Only split a title with an explicit product type and a single color.
HEADPHONE = re.compile(
    r'(?P<model>.+?)\s+(?:(?:Wireless|Wired|Bluetooth|On[- ]Ear|Over[- ]Ear)\s+)*'
    r'Headphones?\s+[-–—]\s+(?P<color>[a-z]+)', re.I)


class StorePage(HTMLParser):
    def __init__(self, body):
        super().__init__()
        self.links = []
        self.forms = []
        self.form = None
        self.feed(body.decode('utf-8'))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and PRODUCT_PATH.fullmatch(attrs.get('href') or ''):
            self.links.append(attrs['href'])
        if tag == 'form':
            self.form = ([], []) if attrs.get('action') == '/cart' and attrs.get('method') == 'post' else None
        if self.form is None:
            return
        if tag == 'input' and attrs.get('name') == 'cartFormInput' and attrs.get('type') == 'hidden':
            self.form[0].append(attrs.get('value'))
        if tag == 'button' and attrs.get('type') == 'submit':
            self.form[1].append('disabled' not in attrs and attrs.get('aria-disabled') != 'true')

    def handle_endtag(self, tag):
        if tag == 'form' and self.form is not None:
            values, enabled = self.form
            if len(values) == 1 and enabled == [True]:
                self.forms.append(json.loads(values[0], object_pairs_hook=_unique_pairs))
            self.form = None


def product_links(body, model):
    tokens = re.findall(r'[a-z0-9]+', model.lower())
    if not tokens:
        return ()
    # Merchants can insert a product type between the manufacturer and model.
    alternatives = {'-'.join(tokens)}
    if len(tokens) == 2:
        alternatives.add('-headset-'.join(tokens))
    return tuple(dict.fromkeys(ORIGIN + path for path in StorePage(body).links
                               if any('-' + slug + '-' in '-' + path.split('/')[-1] + '-'
                                      for slug in alternatives)))[:1]


def nodes(outcome):
    parser = _HTML(None)
    parser.feed(outcome.body.decode('utf-8'))
    return [json.loads(block, parse_float=Decimal, object_pairs_hook=_unique_pairs)
            for block in parser.scripts]


def single(nodes, kind):
    matches = [n for n in nodes if isinstance(n, dict) and n.get('@type') == kind]
    if len(matches) != 1:
        raise ValueError('Ambiguous or missing ' + kind)
    return matches[0]


def policy_answers(outcome, expected_url=FAQ):
    if not outcome or outcome.status != 'ok' or outcome.final_url != expected_url:
        return {}
    try:
        faq = single(nodes(outcome), 'FAQPage')
        return _unique_pairs((q['name'], q['acceptedAnswer']['text']) for q in faq['mainEntity'])
    except (ValueError, KeyError, TypeError):
        return {}


def read_offer(page, policy, request, owner, run, route_id):
    """Accept one current, matching form line and one same-page offer. No cart request is made."""
    url = page.origin_url
    if page.final_url != url or not url.startswith(ORIGIN + '/products/'):
        return None
    data = nodes(page)
    product, store = single(data, 'Product'), single(data, 'Store')
    offer = product['offers']
    title = product['name']
    title_parts = HEADPHONE.fullmatch(title)
    wired = re.fullmatch(r'([A-Za-z]+) Headset ([A-Za-z]+[0-9]+[A-Za-z]*)', title, re.I)
    if ((not title_parts and not wired) or offer['url'] != url or offer['seller']['name'] != owner.entry.name
            or store['name'] != owner.entry.name or owner.entry.name != SELLER or store['url'] != ORIGIN
            or store['address']['addressCountry'] != 'LB' or not store['address'].get('streetAddress')):
        return None
    form, = StorePage(page.body).forms
    line, = form['inputs']['lines']
    selected = line['selectedVariant']
    ident = re.fullmatch(r'gid://shopify/ProductVariant/(\d+)', selected['id'])
    if (form['action'] != 'LinesAdd' or not ident or line['merchandiseId'] != selected['id']
            or line['quantity'] != 1 or selected['availableForSale'] is not True
            or selected['title'] != title or selected['product']['title'] != title
            or urlsplit(url).path != '/products/' + selected['product']['handle']
            or selected['selectedOptions'] or offer['availability'] != 'https://schema.org/InStock'):
        return None
    amount = Decimal(str(offer['price']))
    if (not amount.is_finite() or amount <= 0 or offer['priceCurrency'] != 'USD'
            or selected['price']['currencyCode'] != 'USD' or amount != Decimal(selected['price']['amount'])):
        return None
    pid, vid = selected['product']['handle'], ident.group(1)
    evidence = []

    def receipt(path, excerpt, *, document=page, route=False):
        eid = f'{SOURCE}:{vid}:{path}:{len(evidence)}'
        evidence.append(v2.Evidence(evidence_id=eid, field_path=path, source_id=SOURCE, product_id=pid,
            variant_id=vid, scope='route' if route else 'variant', route_id=route_id if route else None,
            origin_url=document.origin_url, final_url=document.final_url, observed_at=document.observed_at,
            response_sha256=document.response_sha256, adapter_version='itworks-audio/1',
            origin='runtime', corpus_version='domestic-buyer-1', run=run, excerpt=excerpt))
        return [eid]

    def known(path, value, excerpt, *, route=False):
        return dict(state='known', value=value, evidence_ids=receipt(path, excerpt, route=route))

    def unknown(reason):
        return dict(state='unknown', reason=reason)

    maker = product['brand']['name']
    configuration = None
    color_source = title
    if wired:
        description = product.get('description', '')
        # These are the bounded fields of this retailer's plain-text specifications.
        # A two-tone finish or a combined connector must not collapse to its first word.
        colors = re.findall(r'\bColor: ([A-Za-z]+) Performance Audio Type:', description)
        connector = re.search(
            r'\bConnection Type: Wired Connector: (USB Type-A) USB Protocol:|'
            r'\bConnection Type: Wired Connector: (Single 3\.5 mm Audio Jack) Audio & Microphone Connection:',
            description)
        if (wired[1] != maker or description.count('Color:') != 1 or description.count('Connector:') != 1
                or description.count('Connection Type:') != 1 or len(colors) != 1 or connector is None):
            return None
        color = colors[0]
        color_source = 'Color: ' + color
        model = maker + ' ' + wired[2]
        connector_text = 'Connection Type: Wired Connector: ' + (connector[1] or connector[2])
        configuration = identify_headset(maker, wired[2], color, connector='usb-a' if connector[1] else '3.5mm')
    else:
        model, color = title_parts.group('model', 'color')
    if not model.casefold().startswith(maker.casefold() + ' '):
        return None
    receipt('variant.product.model', title)
    receipt('variant.product.manufacturer', maker)
    receipt('seller.name', offer['seller']['name'])
    receipt('seller.market', f"{store['name']} — {store['address']['streetAddress']}, LB")
    condition = product.get('itemCondition')
    if offer.get('itemCondition', condition) != condition:
        return None
    new = known('condition', 'new', condition) if condition == 'https://schema.org/NewCondition' else unknown(
        'The selected product does not explicitly establish new condition')
    delivery = offer.get('shippingDetails', {})
    destination = request.fulfillment.destination
    answers = policy_answers(policy)
    area = answers.get('Do you deliver, and where?', '')
    eligible = (destination.country == 'LB' and delivery.get('shippingDestination') == {
        '@type': 'DefinedRegion', 'addressCountry': 'LB'} and not delivery.get('doesNotShip')
        and set(delivery) <= {'@type','shippingDestination','shippingRate','deliveryTime','doesNotShip'}
        and delivery.get('@type') == 'OfferShippingDetails'
        and area.startswith('Yes — we deliver anywhere in Lebanon. Orders are usually with you within ')
        and not re.search(r'\b(except|exclude\w*|excluding|only)\b', area, re.I))
    service = known('route.item_eligible', True, 'Offer shippingDestination: LB', route=True) if eligible else unknown(
        'Service to this destination is unconfirmed')
    rate = delivery.get('shippingRate', {})
    shipping = dict(kind='shipping', state='unknown', reason='No applicable item delivery rate')
    if eligible and rate.get('currency') == 'USD' and set(rate) == {'@type', 'currency', 'value'}:
        fee = Decimal(str(rate['value']))
        policy_rate = re.fullmatch(r"Delivery is a flat \$(\d+(?:\.\d{1,2})?) anywhere in Lebanon — "
            r"it's added as a separate line at checkout, so the price you see on the product is the price of the product\.",
            answers.get('How much does delivery cost?', ''))
        if (policy_rate and fee.is_finite() and fee >= 0 and rate['@type'] == 'MonetaryAmount'
                and fee == Decimal(policy_rate[1])):
            shipping = dict(kind='shipping', state='additional', amount=dict(currency='USD', amount=fee),
                            evidence_ids=receipt('costs.order.shipping', json.dumps(rate,default=str), route=True)
                            + receipt('costs.order.shipping', policy_rate[0], document=policy, route=True))
    tax = dict(kind='tax', state='unknown', reason='Current seller tax terms are unavailable')
    statement = 'All prices are in USD and include tax (TTC).'
    if (answers.get('What payment methods do you accept?', '').endswith(statement)
            and not offer.get('priceSpecification')):
        tax = dict(kind='tax', state='included', evidence_ids=receipt('costs.order.tax', statement, document=policy))
    product_key = configuration.product_id if configuration else f'{SOURCE}:{pid}'
    variant_key = configuration.variant_id if configuration else f'{SOURCE}:{vid}'
    connectivity = unknown('Connection modes were not checked')
    if wired:
        connectivity = known('variant.specs.connectivity', 'wired', connector_text)
    if configuration:
        receipt('catalog.configuration_id', f'{title}; {color_source}; {connector_text}')
    return v2.Offer(offer_id=f'{SOURCE}:{pid}:{vid}', retailer_id=owner.entry.retailer_id, source_id=SOURCE,
        source_product_id=pid, source_variant_id=vid, seller=dict(seller_id=owner.seller_id, name=SELLER, market='LB'),
        listing_url=url, variant=dict(variant_id=variant_key, product=dict(product_id=product_key,
            category='audio', manufacturer=maker, model=model), color=known('variant.color', color, color_source),
            specs=dict(category='audio', form_factor=known('variant.specs.form_factor','headphones',title),
                       connectivity=connectivity)),
        route=dict(kind='direct', origin_country='ZZ', route_id=route_id, destination=destination, item_eligible=service),
        price=known('price',dict(amount=amount,currency='USD'),f'Selected form and Offer: USD {amount}'),
        stock=known('stock',True,offer['availability']), purchasable=known('purchasable',True,
            f'Enabled purchase form: selected variant {vid}, availableForSale true'), condition=new,
        costs=(tax,shipping), evidence=tuple(evidence))
