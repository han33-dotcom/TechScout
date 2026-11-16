"""Build evidence-bound v2 offers from owned, offline identity recipes."""
from copy import deepcopy
import json
from pathlib import Path

from app.contracts_v2 import Offer

RECIPES = json.loads((Path(__file__).parent / 'fixtures/identity/full-product.json').read_text(
    encoding='utf-8'))


def offer(category='phone', offer_id='left', changes=None):
    recipe = deepcopy(RECIPES[category])
    recipe.update(condition='new', activation='unactivated' if category == 'phone' else None,
                  payment='cash', warranty={'provider': 'Example Service', 'months': 12, 'territory': 'LB'})
    for path, value in (changes or {}).items():
        target = recipe
        parts = path.split('.')
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = value
    receipts = []

    def receipt(path):
        eid = offer_id + ':' + path
        receipts.append(dict(
            schema_version=2, evidence_id=eid, source_id='source-' + offer_id,
            product_id='listing', variant_id='shared-sku', scope='variant', field_path=path,
            origin_url='https://' + offer_id + '.example/product',
            final_url='https://' + offer_id + '.example/product',
            observed_at='2026-11-16T12:00:30Z', response_sha256='a' * 64,
            adapter_version='synthetic-identity-v1', excerpt='Synthetic field: ' + path,
            origin='bootstrap', corpus_version='identity-fixture-1', index_version='identity-fixture-1',
        ))
        return eid

    def fact(path, value):
        if value is None:
            return None
        if isinstance(value, dict) and 'state' in value:
            result = deepcopy(value)
            if result['state'] != 'unknown':
                result['evidence_ids'] = [receipt(path)]
            return result
        return dict(state='known', value=value, evidence_ids=[receipt(path)])

    product = dict(product_id=category + '-product', category=category,
                   manufacturer=recipe['manufacturer'], model=recipe['model'])
    for name in ('manufacturer', 'model'):
        receipt('variant.product.' + name)
    data = dict(
        schema_version=2, offer_id=offer_id, retailer_id='retailer-' + offer_id,
        source_id='source-' + offer_id, source_product_id='listing', source_variant_id='shared-sku',
        seller=dict(seller_id='seller-' + offer_id, name='Synthetic ' + offer_id, market='LB'),
        listing_url='https://' + offer_id + '.example/product',
        variant=dict(schema_version=2, variant_id=category + '-' + offer_id, product=product,
                     specs=dict(category=category, **{name: fact('variant.specs.' + name, value)
                                                       for name, value in recipe['specs'].items()}),
                     color=fact('variant.color', recipe['color'])),
        route=dict(kind='direct', origin_country='LB', destination=dict(country='LB', city='Beirut'),
                   route_id='route-' + offer_id,
                   item_eligible=dict(state='unknown', reason='Route not verified by identity fixture')),
        price=fact('price', {'amount': '900', 'currency': 'USD'}),
        stock=fact('stock', True), purchasable=fact('purchasable', True),
        condition=fact('condition', recipe['condition']), activation=fact('activation', recipe['activation']),
        payment=fact('payment', recipe['payment']),
        warranty={name: fact('warranty.' + name, value) for name, value in recipe['warranty'].items()},
    )
    return Offer.model_validate({**data, 'evidence': receipts})
