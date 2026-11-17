"""PC and Parts exact-listing reader; synthetic-profile validation, not live coverage.

All acquisition uses read_page's gate/accounting boundary. The controller supplies
the reviewed SKU mapping and selected route; page text cannot choose either.
"""
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
import re

from app.contracts import Fact, Money
from app.contracts_v2 import Charge, DirectRoute, Evidence
from app.corpus_contracts import Extraction, FieldObservation, PassageDraft
from app.extraction import PageProfile, extract_structured, _HTML, _objects, _unique_pairs, _excerpt
from app.page_extraction import read_page

SOURCE_ID = 'pcandparts'
ORIGIN = 'https://pcandparts.com'
VERSION = 'pcandparts/1'


@dataclass(frozen=True)
class Listing:
    extraction: Extraction
    part_number: Fact[str]
    package_weight_kg: Fact[str]
    quoted_shipping: Fact[Money]
    costs: tuple[Charge, ...]
    evidence: tuple[Evidence, ...]
    gaps: tuple[str, ...]


def extract_listing(capture, *, sku, category, route: DirectRoute, run=None):
    """Read one exact SKU and one seller offer; ambiguous pages fail closed.

    Shipping rates are quote candidates, never complete delivered totals. Country
    coverage alone cannot bind a zone/weight-dependent rate to a buyer's city.
    """
    if category not in {'laptop', 'monitor'}:
        raise ValueError('PC and Parts profile supports laptops and monitors')
    if route.origin_country != 'LB' or route.destination.country != 'LB':
        raise ValueError('This profile is for domestic direct delivery')
    if capture.key.route_id != route.route_id:
        raise ValueError('Capture must belong to the selected delivery route')
    profile = PageProfile(SOURCE_ID, ORIGIN, sku=sku)
    # Validate capture origin, source, variant and run before parsing extra fields.
    base = extract_structured(capture, profile, run=run)
    parsed = _HTML(None)
    parsed.feed(capture.body.decode('utf-8'))
    matches = []
    for block, script in enumerate(parsed.scripts):
        try:
            data = json.loads(script, parse_float=Decimal, object_pairs_hook=_unique_pairs,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
            for path, product in _objects(data):
                if product.get('@type') == 'Product' and product.get('sku') == sku:
                    matches.append((block, path, product, script))
        except (ValueError, RecursionError):
            continue
    if len(matches) != 1:
        raise ValueError('One unambiguous exact-SKU Product is required')
    block, path, product, script = matches[0]
    if product.get('url') not in (None, str(capture.key.url), str(capture.final_url)):
        raise ValueError('Product URL does not match the captured listing')
    offers = product.get('offers', [])
    offers = offers if isinstance(offers, list) else [offers]
    if len(offers) != 1 or not isinstance(offers[0], dict) or offers[0].get('@type') != 'Offer':
        raise ValueError('One exact seller Offer is required; aggregate offers are unsupported')
    offer = offers[0]
    if offer.get('sku') not in (None, sku) or offer.get('url') not in (
            None, str(capture.key.url), str(capture.final_url)):
        raise ValueError('Offer does not belong to the selected listing')
    seller = offer.get('seller')
    if seller is not None and (not isinstance(seller, dict) or seller.get('name') != 'PC and Parts'
                              or seller.get('url') not in (None, ORIGIN, ORIGIN + '/')):
        raise ValueError('Unexpected seller; this is not a marketplace profile')
    prefix = f'/jsonld/{block}{path}'
    offer_path = prefix + '/offers' + ('/0' if isinstance(product.get('offers'), list) else '')
    observations, passages = list(base.observations), list(base.passages)
    extra = []
    digest = sha256(capture.body).hexdigest()

    def receipt(field, key, value, pointer, *, routed=False):
        excerpt = _excerpt(script, key, value)
        identity = json.dumps([capture.key.model_dump(mode='json'), digest,
            capture.observed_at.isoformat(), pointer, field, VERSION,
            capture.processing.model_dump(), run.model_dump() if run else None], sort_keys=True)
        result = Evidence(evidence_id=sha256(identity.encode()).hexdigest(), source_id=SOURCE_ID,
            product_id=capture.key.product_id, variant_id=sku, scope='route' if routed else 'variant',
            route_id=route.route_id if routed else None, field_path=field,
            origin_url=capture.key.url, final_url=capture.final_url, observed_at=capture.observed_at,
            response_sha256=digest, adapter_version=VERSION, json_pointer=pointer, excerpt=excerpt,
            origin=capture.origin, corpus_version=capture.processing.corpus_version,
            index_version=capture.processing.index_version, run=run)
        extra.append(result)
        passages.append(PassageDraft(locator=pointer, text=excerpt, evidence=result))
        return result.evidence_id

    def unknown(reason):
        return {'state': 'unknown', 'reason': reason}

    part = unknown('Exact manufacturer part number absent')
    if isinstance(product.get('mpn'), str) and product['mpn'].strip():
        part = {'state': 'known', 'value': product['mpn'], 'evidence_ids': [
            receipt('part_number', 'mpn', product['mpn'], prefix + '/mpn')]}

    # Only explicit structured attributes, never specifications inferred from names.
    properties = product.get('additionalProperty', [])
    properties = properties if isinstance(properties, list) else []
    if len(properties) > 100:
        raise ValueError('Too many product attributes')
    fields = {}
    mapping = ({'CPU': 'cpu', 'GPU': 'gpu', 'Operating system': 'operating_system'}
               if category == 'laptop' else {'Panel': 'panel', 'Width': 'width_px',
                   'Height': 'height_px', 'Refresh rate': 'refresh_hz', 'Diagonal': 'diagonal_inches'})
    weights = []
    for i, prop in enumerate(properties):
        if not isinstance(prop, dict) or not isinstance(prop.get('value'), str):
            continue
        name, value = prop.get('name'), prop['value']
        if not isinstance(name, str) or not value.strip():
            continue
        pointer = prefix + f'/additionalProperty/{i}/value'
        if name == 'Shipping weight' and re.fullmatch(r'[0-9]+(?:\.[0-9]+)? kg', value):
            if Decimal(value.split()[0]) > 0:
                weights.append((str(Decimal(value.split()[0])), receipt('package_weight_kg', 'value', value, pointer)))
        field = mapping.get(name)
        if field is None:
            continue
        normalized = value
        if field in {'width_px', 'height_px', 'refresh_hz'}:
            unit = 'Hz' if field == 'refresh_hz' else 'px'
            if not re.fullmatch(r'[1-9][0-9]{0,5} ' + unit, value):
                continue
            normalized = int(value.split()[0])
        elif field == 'diagonal_inches':
            if not re.fullmatch(r'[1-9][0-9]?(?:\.[0-9]+)? inches', value):
                continue
            normalized = value.split()[0]
        field = 'variant.specs.' + field
        fields.setdefault(field, []).append((normalized, receipt(field, 'value', value, pointer)))

    def fact(values, reason):
        if not values:
            return unknown(reason)
        unique = list(dict.fromkeys(value for value, _ in values))
        result = {'evidence_ids': [eid for _, eid in values]}
        return result | ({'state': 'known', 'value': unique[0]} if len(unique) == 1 else
                         {'state': 'conflict', 'alternatives': unique, 'reason': 'Listing attributes disagree'})

    for field, values in fields.items():
        ids = {eid for _, eid in values}
        observations.append(FieldObservation(fact=fact(values, 'Attribute absent'),
                                             evidence=tuple(e for e in extra if e.evidence_id in ids)))
    brand = product.get('brand')
    if isinstance(brand, dict) and isinstance(brand.get('name'), str) and brand['name'].strip():
        eid = receipt('variant.product.manufacturer', 'name', brand['name'], prefix + '/brand/name')
        observations.append(FieldObservation(fact={'state': 'known', 'value': brand['name'],
            'evidence_ids': [eid]}, evidence=(extra[-1],)))
    weight = fact(weights, 'Packaged shipping weight absent; product weight is insufficient')
    tax = Charge(kind='tax', state='unknown', reason='Item-specific tax inclusion/amount absent')
    spec = offer.get('priceSpecification')
    if isinstance(spec, dict) and spec.get('valueAddedTaxIncluded') is True:
        # Reject a price specification that describes a different payable price.
        if spec.get('price') in (None, offer.get('price')) and spec.get('priceCurrency') in (None, offer.get('priceCurrency')):
            eid = receipt('costs.order.tax', 'valueAddedTaxIncluded', True,
                          offer_path + '/priceSpecification/valueAddedTaxIncluded')
            tax = Charge(kind='tax', state='included', evidence_ids=(eid,))
    quote = unknown('No unique city/postcode-specific shipping rate with packaged weight')
    details = offer.get('shippingDetails', [])
    details = details if isinstance(details, list) else [details]
    candidates = []
    if len(details) > 50:
        raise ValueError('Too many delivery alternatives')
    for i, detail in enumerate(details):
        if not isinstance(detail, dict) or weight.get('state') != 'known':
            continue
        destination, rate = detail.get('shippingDestination'), detail.get('shippingRate')
        if not isinstance(destination, dict) or not isinstance(rate, dict):
            continue
        # addressRegion is not a city. Only exact addressLocality/postalCode counts.
        if (destination.get('addressCountry') != 'LB' or not route.destination.city
                or destination.get('addressLocality') != route.destination.city
                or destination.get('postalCode') != route.destination.postal_code):
            continue
        amount = rate.get('value')
        if type(amount) not in (str, int, Decimal) or rate.get('currency') != offer.get('priceCurrency'):
            continue
        try:
            money = Money(amount=str(amount), currency=rate['currency'])
        except ValueError:
            continue
        pointer = offer_path + '/shippingDetails' + (f'/{i}' if isinstance(offer.get('shippingDetails'), list) else '')
        eid = receipt('quoted_shipping', 'value', amount, pointer + '/shippingRate/value', routed=True)
        candidates.append((money, eid))
    if len(candidates) == 1:
        quote = {'state': 'known', 'value': candidates[0][0], 'evidence_ids': [candidates[0][1]]}
    shipping = Charge(kind='shipping', state='unknown',
        reason='Published rate is not a complete zone/weight quote; surcharge and applicability checks remain')
    gaps = ['purchasability', 'delivery eligibility', 'complete shipping and surcharges', 'live source validation']
    present = {o.evidence[0].field_path: o.fact.state for o in observations}
    gaps.extend(field for field in ('price', 'stock', 'condition', 'variant.product.model')
                if present.get(field) != 'known')
    if tax.state == 'unknown':
        gaps.append('item-specific tax')
    if part['state'] != 'known':
        gaps.append('manufacturer part number')
    if weight['state'] != 'known':
        gaps.append('packaged weight')
    return Listing(Extraction(observations=tuple(observations), passages=tuple(passages)),
        Fact[str].model_validate(part), Fact[str].model_validate(weight),
        Fact[Money].model_validate(quote), (tax, shipping), tuple(extra), tuple(gaps))


def read_listing(gate, job, key, processing, *, category, route, origin, capability=None, run=None):
    """One controller-authorized page through the shared gate and ledger; no fallback activation."""
    if (key.source_id != SOURCE_ID or key.url.host != 'pcandparts.com' or key.url.port != 443
            or not key.product_id or not key.variant_id or key.branch_id
            or key.route_id != route.route_id or category not in {'laptop', 'monitor'}
            or route.origin_country != 'LB' or route.destination.country != 'LB'):
        raise ValueError('Expected a reviewed PC and Parts variant and domestic route')
    parsed = []

    def dedicated(capture):
        listing = extract_listing(capture, sku=key.variant_id, category=category, route=route, run=run)
        parsed.append(listing)
        return listing.extraction

    page = read_page(gate, job, key, PageProfile(SOURCE_ID, ORIGIN, sku=key.variant_id), processing,
                     origin=origin, dedicated=dedicated, capability=capability, run=run)
    return page, parsed[0] if parsed else None
