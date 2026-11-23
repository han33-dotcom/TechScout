"""Deterministic shared metadata extraction. Page text never controls tools."""
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from html.parser import HTMLParser
import json
import re
from urllib.parse import urlsplit

from app.contracts_v2 import Evidence, RunReference
from app.corpus_contracts import Extraction, FieldObservation, PassageDraft, DocumentCapture


@dataclass(frozen=True)
class PageProfile:
    """Reviewed source binding, not a platform-wide claim of coverage."""
    source_id: str
    origin: str
    sku: str | None = None
    policy_element_id: str | None = None
    policy_field: str = 'policy.shipping'
    seller_names: tuple[str, ...] = ()
    category: str | None = None


class _HTML(HTMLParser):
    def __init__(self, policy_id):
        super().__init__(convert_charrefs=False)
        self.scripts, self.policy, self.script, self.policy_depth = [], [], None, 0
        self.policy_id = policy_id

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script' and attrs.get('type','').lower() == 'application/ld+json':
            self.script = []
        if self.policy_depth and tag not in {'br','hr','img','input','meta','link','source','wbr'}:
            self.policy_depth += 1
        elif self.policy_id and attrs.get('id') == self.policy_id:
            self.policy_depth = 1

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if tag == 'script' and self.script is not None:
            self.scripts.append(''.join(self.script))
            self.script = None
        if self.policy_depth:
            self.policy_depth -= 1

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)
        elif self.policy_depth and data.strip():
            self.policy.append(data.strip())


def _objects(value, path='', depth=0):
    if depth > 20:
        raise ValueError('Structured metadata nesting exceeds its bound')
    if isinstance(value, list):
        for i, item in enumerate(value):
            yield from _objects(item, f'{path}/{i}', depth+1)
    elif isinstance(value, dict):
        yield path, value
        for key in ('@graph', 'hasVariant'):
            if key in value:
                yield from _objects(value[key], f'{path}/{key}', depth+1)


def _literal(value):
    return str(value) if isinstance(value,Decimal) else json.dumps(value,ensure_ascii=False)


def _unique_pairs(pairs):
    result = {}
    for key,value in pairs:
        if key in result:
            raise ValueError('Duplicate metadata keys cannot establish a fact')
        result[key] = value
    return result


def _excerpt(script, key, value):
    # Keep the actual source spelling/whitespace, never a model-generated quote.
    pattern = re.escape(json.dumps(key)) + r'\s*:\s*' + re.escape(_literal(value))
    match = re.search(pattern,script)
    if not match and isinstance(value, str):
        strings = re.escape(json.dumps(key)) + r'\s*:\s*("(?:[^"\\]|\\.)*")'
        match = next((m for m in re.finditer(strings,script) if json.loads(m.group(1))==value), None)
    if not match or len(match.group()) > 500:
        raise ValueError('No bounded original field excerpt')
    return match.group()


def _selected_products(scripts, profile, capture):
    from app.catalog_extraction import current_unrestricted_offer, matches_page, offer_seller_matches

    selected = []
    for index, script in enumerate(scripts):
        try:
            data = json.loads(script,parse_float=Decimal,object_pairs_hook=_unique_pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite metadata')))
        except (ValueError, RecursionError):
            continue
        for path, product in _objects(data):
            if product.get('@type') != 'Product' or str(product.get('sku','')) != profile.sku:
                continue
            if profile.seller_names:
                offers = product.get('offers')
                if isinstance(offers,list):
                    if len(offers)!=1:
                        continue
                    offers = offers[0]
                if not (matches_page(product,capture) and isinstance(offers,dict) and offers.get('@type')=='Offer'
                        and offers.get('sku') in (None,profile.sku) and matches_page(offers,capture)
                        and offer_seller_matches(offers,profile.seller_names)
                        and current_unrestricted_offer(offers,capture.observed_at)):
                    continue
                if selected:
                    return ()  # Two presentations of the same SKU cannot supply complementary offer facts.
            selected.append((script,f'/jsonld/{index}{path}',product))
    return selected


def extract_structured(capture: DocumentCapture, profile: PageProfile, *, run: RunReference | None = None,
                       destination=None):
    origin = urlsplit(profile.origin)
    if (capture.key.source_id != profile.source_id or origin.scheme != 'https'
        or any((url.scheme,url.host,url.port) != ('https',origin.hostname,origin.port or 443)
               for url in (capture.key.url,capture.final_url))):
        raise ValueError('Source profile does not match this capture')
    if capture.visibility != 'public' or (run and capture.origin != 'runtime'):
        raise ValueError('Extraction requires an approved public capture and coherent run origin')
    text = capture.body.decode('utf-8')
    parsed = _HTML(profile.policy_element_id)
    parsed.feed(text)
    if len(parsed.scripts) > 50:
        raise ValueError('Too many structured metadata blocks')
    digest = sha256(capture.body).hexdigest()
    fields = {}

    def add(field, value, excerpt, pointer, *, source=False, route=False):
        key = capture.key
        identity = json.dumps([digest,key.model_dump(mode='json'),capture.observed_at.isoformat(),
                               field,pointer,capture.processing.model_dump(),run.model_dump() if run else None],sort_keys=True)
        receipt = Evidence(evidence_id=sha256(identity.encode()).hexdigest(),source_id=key.source_id,
            product_id=None if source else key.product_id,variant_id=None if source else key.variant_id,
            scope='source' if source else 'route' if route else 'variant',
            route_id=(key.route_id or 'catalog-dispatch') if route else None,
            field_path=field,origin_url=key.url,final_url=capture.final_url,
            observed_at=capture.observed_at,response_sha256=digest,adapter_version=capture.processing.extraction_version,
            json_pointer=pointer,excerpt=excerpt,origin=capture.origin,corpus_version=capture.processing.corpus_version,
            index_version=capture.processing.index_version,run=run)
        fields.setdefault(field,[]).append((value,receipt))

    if profile.policy_element_id:
        for i, text_chunk in enumerate(parsed.policy):
            # Policies are source text only. No automatic fee/deadline guarantee.
            if len(text_chunk) <= 500 and text_chunk in text:
                add(profile.policy_field,text_chunk,text_chunk,f'/element/{profile.policy_element_id}/{i}',source=True)
    if profile.sku:
        if not capture.key.product_id or capture.key.variant_id != profile.sku or capture.key.branch_id:
            raise ValueError('Listing extraction requires an explicit product/variant binding')
        for script, prefix, product in _selected_products(parsed.scripts,profile,capture):
            if profile.seller_names:
                from app.catalog_extraction import product_identity, route_facts
                product_identity(product,script,prefix,add)
            for key, field in (('color','variant.color'),('model','variant.product.model')):
                value = product.get(key)
                if isinstance(value,str) and value.strip():
                    add(field,value,_excerpt(script,key,value),prefix+'/'+key)
            offers = product.get('offers',[])
            offer_array = isinstance(offers,list)
            offers = offers if isinstance(offers,list) else [offers]
            for i, offer in enumerate(offers):
                if not isinstance(offer,dict) or offer.get('@type') != 'Offer':
                    continue
                if not profile.seller_names and (offer.get('sku') not in (None,profile.sku)
                        or offer.get('url') not in (None,str(capture.key.url),str(capture.final_url))):
                    continue
                suffix = prefix + '/offers' + (f'/{i}' if offer_array else '')
                if profile.seller_names:
                    route_facts(offer, script, suffix, add, destination)
                price, currency = offer.get('price'), offer.get('priceCurrency')
                if type(price) in (str,int,Decimal) and isinstance(currency,str) and currency in {'USD','LBP','EUR','AED'}:
                    try:
                        amount = Decimal(price)
                        if amount.is_finite() and amount > 0:
                            add('price',{'amount':str(amount),'currency':currency},
                                _excerpt(script,'price',price),suffix+'/price')
                            if profile.seller_names:
                                add('price',{'amount':str(amount),'currency':currency},
                                    _excerpt(script,'priceCurrency',currency),suffix+'/priceCurrency')
                    except (ValueError,ArithmeticError):
                        pass
                stock = {'https://schema.org/InStock':True, 'https://schema.org/OutOfStock':False,
                         'http://schema.org/InStock':True, 'http://schema.org/OutOfStock':False}
                if isinstance(offer.get('availability'),str) and offer['availability'] in stock:
                    add('stock',stock[offer['availability']],_excerpt(script,'availability',offer['availability']),suffix+'/availability')
                if (profile.seller_names and capture.origin == 'maintenance' and
                        offer.get('availability') in ('https://schema.org/Discontinued', 'http://schema.org/Discontinued')):
                    add('resource.availability', 'discontinued', _excerpt(script, 'availability', offer['availability']),
                        suffix + '/availability')
                conditions = {'https://schema.org/NewCondition':'new','https://schema.org/UsedCondition':'used',
                              'https://schema.org/RefurbishedCondition':'refurbished'}
                if isinstance(offer.get('itemCondition'),str) and offer['itemCondition'] in conditions:
                    add('condition',conditions[offer['itemCondition']],_excerpt(script,'itemCondition',offer['itemCondition']),suffix+'/itemCondition')
            properties = product.get('additionalProperty',[])
            for i, prop in enumerate(properties if isinstance(properties,list) else []):
                if not isinstance(prop,dict):
                    continue
                name = prop.get('name')
                field = {'RAM':'variant.specs.ram_gb','Storage':'variant.specs.storage_gb'}.get(name) if isinstance(name,str) else None
                raw = prop.get('value')
                if profile.category == 'monitor' and name == 'Refresh Rate' and isinstance(raw, str):
                    match = re.fullmatch(r'([1-9][0-9]{0,3}) Hz', raw)
                    if match:
                        add('variant.specs.refresh_hz', int(match[1]), _excerpt(script,'value',raw),
                            prefix+f'/additionalProperty/{i}/value')
                if profile.category == 'audio' and name == 'Form Factor' and isinstance(raw,str):
                    if raw.casefold() in ('earbuds','headphones'):
                        add('variant.specs.form_factor', raw.casefold(), _excerpt(script,'value',raw),
                            prefix+f'/additionalProperty/{i}/value')
                if profile.seller_names and name == 'Purchasable' and type(raw) is bool:
                    add('purchasable',raw,_excerpt(script,'value',raw),prefix+f'/additionalProperty/{i}/value')
                if field and isinstance(raw,str) and re.fullmatch(r'[1-9][0-9]{0,5} GB',raw):
                    add(field,int(raw.split()[0]),_excerpt(script,'value',raw),prefix+f'/additionalProperty/{i}/value')
    observations, passages = [], []
    for field, values in fields.items():
        unique = []
        for value,_ in values:
            if value not in unique:
                unique.append(value)
        receipts = tuple({r.evidence_id:r for _,r in values}.values())
        fact = dict(evidence_ids=[r.evidence_id for r in receipts])
        fact.update(state='known',value=unique[0]) if len(unique)==1 else fact.update(
            state='conflict',alternatives=unique,reason='Structured page values disagree for this field and variant')
        observations.append(FieldObservation(fact=fact,evidence=receipts))
        # Catalog prices, inventory and shipping rates are structured temporal
        # observations. Only stable specification text belongs in the vector index.
        if profile.seller_names and not field.startswith('variant.'):
            continue
        for receipt in receipts:
            passages.append(PassageDraft(locator=receipt.json_pointer,text=receipt.excerpt,evidence=receipt))
    return Extraction(observations=tuple(observations),passages=tuple(passages))


def extract_page(capture, profile, *, dedicated=None, run=None):
    """Dedicated parser wins; a shared platform does not override its evidence."""
    return dedicated(capture) if dedicated is not None else extract_structured(capture,profile,run=run)


def verified_price_order(offers):
    """Compare already verified offers for one configuration, independent of parser."""
    from app.contracts_v2 import VerifiedOffer
    offers = tuple(VerifiedOffer.model_validate(o.model_dump(mode='json')) for o in offers)
    groups = {(o.offer.variant.variant_id,o.cost.total.currency) for o in offers}
    if len(groups)>1:
        raise ValueError('Compare only the same variant in one currency after verification')
    return tuple(sorted(offers,key=lambda o:(o.cost.total.amount,o.offer.offer_id)))
