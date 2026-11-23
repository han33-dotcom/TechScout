"""Bind a pickup-only structured offer to one store, variant and current page."""
from hashlib import sha256
from urllib.parse import urlsplit

from app import contracts_v2 as v2
from app.extraction import PageProfile, _HTML, _excerpt, _selected_products


def select_route(offer, capture, listing, fulfillment):
    """A single pickup location can scope its offer's inventory. Online stock alone cannot."""
    # Without a pickup delivery method there is no branch-specific stock assertion.
    if b'availableDeliveryMethod' not in capture.body:
        return offer if fulfillment.mode != 'pickup' else None
    parser = _HTML(None)
    parser.feed(capture.body.decode('utf-8'))
    origin = 'https://' + urlsplit(listing.listing_url).netloc
    profile = PageProfile(listing.source_id, origin, sku=listing.sku,
                          seller_names=(listing.seller_name, *listing.seller_aliases))
    selected = _selected_products(parser.scripts, profile, capture)
    if len(selected) != 1:
        return offer if fulfillment.mode != 'pickup' else None
    script, prefix, product = selected[0]
    node = product['offers']
    prefix += '/offers'
    if isinstance(node, list):
        node = node[0]
        prefix += '/0'
    method = node.get('availableDeliveryMethod')
    onsite = ('https://schema.org/OnSitePickup','http://schema.org/OnSitePickup')
    if isinstance(method, list) and len(method) == 1:
        method = method[0]
    if method not in onsite:
        return offer if fulfillment.mode != 'pickup' else None
    place = node.get('availableAtOrFrom')
    if fulfillment.mode == 'delivery' or not isinstance(place, dict) or 'shippingDetails' in node:
        return None
    address = place.get('address')
    branch = place.get('branchCode')
    if (not isinstance(branch, str) or not branch.strip() or fulfillment.branch_id not in (None, branch)
            or not isinstance(address, dict) or address.get('addressCountry') not in ('LB','Lebanon')):
        return None
    destination = v2.DeliveryDestination(country='LB', city=address.get('addressLocality'),
                                          postal_code=address.get('postalCode'))
    if any(wanted and (getattr(destination, key) or '').casefold() != wanted.casefold()
           for key, wanted in fulfillment.destination.model_dump().items()):
        return None
    if offer.stock.state != 'known':
        return None
    route = v2.PickupRoute(kind='pickup', route_id='pickup:' + listing.retailer_id + ':' + branch,
                           branch_id=branch, destination=destination)
    original = {e.evidence_id: e for e in offer.evidence}
    stock_record = original[offer.stock.evidence_ids[0]]
    if (node.get('availability') not in ('https://schema.org/InStock', 'http://schema.org/InStock',
                                         'https://schema.org/OutOfStock', 'http://schema.org/OutOfStock')
            or stock_record.json_pointer != prefix + '/availability'):
        return None
    # The branch, SKU, pickup method and availability must be in the same selected Offer.
    fields = [('availableDeliveryMethod', method, '/availableDeliveryMethod'),
              ('branchCode', branch, '/availableAtOrFrom/branchCode'),
              ('addressCountry', address['addressCountry'], '/availableAtOrFrom/address/addressCountry'),
              ('availability', node.get('availability'), '/availability')]
    for name in ('addressLocality', 'postalCode'):
        if address.get(name):
            fields.append((name, address[name], '/availableAtOrFrom/address/' + name))
    receipts = []
    for name, value, path in fields:
        pointer = prefix + path
        # Array-valued delivery methods need their own element locator and verbatim spelling.
        if name == 'availableDeliveryMethod' and isinstance(node[name], list):
            return None
        identity = sha256((stock_record.evidence_id + route.route_id + pointer).encode()).hexdigest()
        receipts.append(stock_record.model_copy(update=dict(evidence_id=identity, branch_id=branch,
                        route_id=route.route_id, scope='branch', json_pointer=pointer, excerpt=_excerpt(script,name,value))))
    data = offer.model_dump()
    data.update(route=route.model_dump(), stock=dict(state='known', value=offer.stock.value,
                evidence_ids=[e.evidence_id for e in receipts]),
                costs=[c.model_dump() for c in offer.costs if c.kind != 'shipping'],
                evidence=[e.model_dump() for e in offer.evidence if e.scope == 'variant' and e.field_path != 'stock']
                         + [e.model_dump() for e in receipts])
    return v2.Offer.model_validate(data)
