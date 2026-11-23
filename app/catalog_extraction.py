"""Item-scoped structured evidence for domestic catalog listings."""
from datetime import date, datetime
from decimal import Decimal
import re
from urllib.parse import urldefrag, urljoin

from app.directory import normalized_alias
from app.extraction import _excerpt


def normalized_gtin(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:[0-9]{8}|[0-9]{12,14})', value):
        return None
    if len(set(value)) == 1:
        return None
    total = sum(int(char) * (3 if i % 2 else 1) for i, char in enumerate(reversed(value)))
    return value.zfill(14) if total % 10 == 0 else None


def product_identity(product, script, prefix, add):
    brand = product.get('brand', product.get('manufacturer'))
    name = brand.get('name') if isinstance(brand, dict) else brand
    if isinstance(name, str) and name.strip():
        key = 'brand' if 'brand' in product else 'manufacturer'
        pointer = f'{prefix}/{key}' + ('/name' if isinstance(brand, dict) else '')
        add('variant.product.manufacturer', name, _excerpt(script, 'name' if isinstance(brand, dict) else key, name), pointer)
    if not product.get('model') and isinstance(product.get('name'), str) and product['name'].strip():
        add('variant.product.model', product['name'], _excerpt(script, 'name', product['name']), prefix+'/name')
    mpn = product.get('mpn')
    if (isinstance(mpn, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9./ _-]{1,79}', mpn)
            and any(c.isdigit() for c in mpn) and any(c.isalpha() for c in mpn)):
        add('catalog.mpn', mpn, _excerpt(script, 'mpn', mpn), prefix+'/mpn')
    for field in ('gtin', 'gtin8', 'gtin12', 'gtin13', 'gtin14'):
        value = product.get(field)
        gtin = normalized_gtin(value)
        if gtin and (field == 'gtin' or str(len(value)) == field[4:]):
            add('catalog.gtin', gtin, _excerpt(script, field, value), prefix+'/'+field)


def matches_page(node, capture):
    urls = {str(capture.key.url), str(capture.final_url)}
    for field in ('url', '@id'):
        if field not in node:
            continue
        value = node[field]
        if not isinstance(value,str) or not value.strip():
            return False
        resolved, _ = urldefrag(urljoin(str(capture.final_url),value))
        if resolved not in urls:
            return False
    return True


def current_unrestricted_offer(offer, observed_at):
    if any(key in offer for key in ('eligibleCustomerType','eligibleDuration','eligibleQuantity',
           'eligibleRegion','ineligibleRegion','eligibleTransactionVolume','addOn')):
        return False
    for key in ('priceValidUntil','validThrough','validFrom','availabilityStarts','availabilityEnds'):
        if key not in offer:
            continue
        value = offer[key]
        if not isinstance(value,str):
            return False
        try:
            if len(value)==10:
                bound, observed = date.fromisoformat(value), observed_at.date()
            else:
                bound, observed = datetime.fromisoformat(value), observed_at
                if bound.tzinfo is None:
                    return False
        except ValueError:
            return False
        if key in ('validFrom','availabilityStarts'):
            if observed<bound:
                return False
        elif observed>bound:
            return False
    return True


def offer_seller_matches(offer, seller_names):
    aliases = {normalized_alias(s) for s in seller_names}
    for field in ('seller','offeredBy'):
        if field not in offer:
            continue
        seller = offer[field]
        name = seller.get('name') if isinstance(seller,dict) else seller
        if not isinstance(name,str) or normalized_alias(name) not in aliases:
            return False
    return True


def _country(value):
    if not isinstance(value, str):
        return None
    if value.casefold() == 'lebanon':
        return 'LB'
    return value if re.fullmatch('[A-Z]{2}', value) else None


def route_facts(offer, script, prefix, add, destination):
    spec = offer.get('priceSpecification')
    if (isinstance(spec, dict) and spec.get('valueAddedTaxIncluded') is True
            and set(spec) <= {'@type','price','priceCurrency','valueAddedTaxIncluded'}
            and spec.get('priceCurrency', offer.get('priceCurrency')) == offer.get('priceCurrency')
            and str(spec.get('price', offer.get('price'))) == str(offer.get('price'))):
        add('costs.order.tax', 'included', _excerpt(script,'valueAddedTaxIncluded',True),
            prefix+'/priceSpecification/valueAddedTaxIncluded')
    shipping = offer.get('shippingDetails')
    path = prefix + '/shippingDetails'
    if isinstance(shipping, list):
        if len(shipping) != 1:
            return
        shipping = shipping[0]
        path += '/0'
    if not isinstance(shipping, dict):
        return
    origin = shipping.get('shippingOrigin')
    if isinstance(origin, dict):
        raw = origin.get('addressCountry')
        country = _country(raw)
        if country:
            add('route.origin_country', country, _excerpt(script, 'addressCountry', raw),
                path+'/shippingOrigin/addressCountry', route=True)
    target = shipping.get('shippingDestination')
    if (not isinstance(target, dict) or set(target) - {'@type','addressCountry','addressLocality'}
            or set(shipping) - {'@type','shippingOrigin','shippingDestination','doesNotShip','shippingRate','deliveryTime'}
            or type(shipping.get('doesNotShip',False)) is not bool):
        return
    country = _country(target.get('addressCountry'))
    city = target.get('addressLocality')
    if not country or (city is not None and (not isinstance(city, str) or not city.strip())):
        return
    field = 'addressLocality' if city else 'addressCountry'
    excerpt = _excerpt(script, field, target[field])
    pointer = path + '/shippingDestination/' + field
    allowed = not shipping.get('doesNotShip', False)
    if allowed:
        add('catalog.delivery_country', country, _excerpt(script,'addressCountry',target['addressCountry']),
            path+'/shippingDestination/addressCountry')
        if city:
            add('catalog.delivery_city', city, excerpt, pointer)
    if not destination or destination.country != country or (city and normalized_alias(city) != normalized_alias(destination.city or '')):
        return
    add('route.item_eligible', allowed, excerpt if allowed else _excerpt(script,'doesNotShip',True),
        pointer if allowed else path+'/doesNotShip', route=True)
    rate = shipping.get('shippingRate')
    if (allowed and isinstance(rate, dict) and set(rate) <= {'@type','minValue','maxValue','currency'}
            and rate.get('currency') == offer.get('priceCurrency') and rate.get('currency') in ('USD','LBP','EUR','AED')
            and all(type(rate.get(k)) in (str, int, Decimal) for k in ('minValue','maxValue'))):
        try:
            low, high = Decimal(rate['minValue']), Decimal(rate['maxValue'])
            if not low.is_finite() or not high.is_finite() or not 0 <= low <= high:
                return
        except ArithmeticError:
            return
        estimate = dict(status='estimated', lower=dict(amount=str(low), currency=rate['currency']),
                        upper=dict(amount=str(high), currency=rate['currency']),
                        assumptions=['Retailer shipping range for this item and destination; final fee unconfirmed.'])
        for key in ('currency','minValue','maxValue'):
            add('costs.order.shipping_range', estimate, _excerpt(script,key,rate[key]),
                path + '/shippingRate/' + key, route=True)
        return
    if (allowed and isinstance(rate, dict) and set(rate) <= {'@type','currency','value'}
            and isinstance(rate.get('currency'),str) and rate.get('currency') == offer.get('priceCurrency') and rate.get('currency') in {'USD','LBP','EUR','AED'}
            and type(rate.get('value')) in (str,int,Decimal)):
        try:
            amount = Decimal(rate['value'])
        except ArithmeticError:
            return
        if amount.is_finite() and amount >= 0:
            money = {'amount': str(amount), 'currency':rate['currency']}
            add('costs.order.shipping', money, _excerpt(script,'value',rate['value']),path+'/shippingRate/value',route=True)
            add('costs.order.shipping', money, _excerpt(script,'currency',rate['currency']),path+'/shippingRate/currency',route=True)
